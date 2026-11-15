import { useCallback, useEffect, useLayoutEffect, useRef } from 'react'

import { listSidebarSessions, type SessionInfo } from '@/hermes'
import { sameCronSignature } from '@/lib/session-signatures'
import { gatewayActivationEpoch } from '@/store/gateway'
import {
  $pinnedSessionIds,
  $sessionsLimit,
  $sidebarFiltersActive,
  bumpSessionsLimit,
  raiseSessionsLimit,
  SIDEBAR_FILTERED_PAGE_SIZE
} from '@/store/layout'
import { normalizeProfileKey, sidebarProfileForScope } from '@/store/profile'
import {
  $selectedStoredSessionId,
  $sessions,
  carryForwardFailedProfileSessions,
  CRON_SECTION_LIMIT,
  keepFailedProfileMeta,
  mergeSessionPage,
  setCorruptSessionStores,
  setCronSessions,
  setSessionProfilesFailed,
  setSessionProfilesTruncated,
  setSessionProfilesUsage,
  setSessions,
  setSessionsLoadError,
  setSessionsLoading
} from '@/store/session'
import {
  $removedSessionIds,
  captureSessionTombstoneGenerations,
  sessionRemovalIntersected,
  type SessionTombstoneGenerationSnapshot,
  tombstoneRowIds
} from '@/store/session-removal'
import { $sessionTiles, $workingSessionIds, getRecentlySettledSessionIds } from '@/store/session-states'

import { refreshCronJobs as refreshCronJobsStore } from '../../cron/cron-actions'

// Keep conversation history separate from cron and transient auxiliary runs.
const SIDEBAR_EXCLUDED_SOURCES = [
  'acp',
  'cron',
  'oneshot',
  'subagent',
  'tool'
]

// Drop rows the user just deleted/archived: ANY list fetch (full refresh,
// "Load more" paging or the cron slice) can race
// an in-flight delete RPC, and the backend page still carries the doomed row
// until the DELETE commits — so it flashed back into the sidebar (#50928).
// Honoring the optimistic tombstone at every ingestion point keeps the removal
// stable; the tombstone self-clears once projects.tree confirms the delete,
// and a failed delete untombstones immediately, so nothing is filtered on the
// non-destructive paths. A tombstone matches the row on ANY id the
// conversation has answered to (tip, root, and every intermediate lineage
// segment) — armed on one name, it must still catch the same conversation
// returning under another (#123685).
function dropTombstoned(sessions: SessionInfo[]): SessionInfo[] {
  const tombstones = $removedSessionIds.get()

  if (!tombstones.size) {
    return sessions
  }

  const tombstoned = (session: SessionInfo): boolean => tombstoneRowIds(session).some(id => tombstones.has(id))

  const kept = sessions.filter(session => !tombstoned(session))

  return kept.length === sessions.length ? sessions : kept
}

// A fetch whose page was READ before an archive/delete committed can land
// after the projects.tree prune has already dropped the tombstone (the tree is
// right to prune — the id is gone from its snapshot), so `dropTombstoned` has
// nothing left to honor and the stale row resurrected through the keep set
// (#123685). The tombstone's generation counter survives the prune: capture it
// at fetch start, and reject any row whose removal lifecycle moved toward
// removal underneath the request. A release edge (failed RPC, unarchive)
// re-admits the row as before.
function dropRemovalRaced(sessions: SessionInfo[], snapshot: SessionTombstoneGenerationSnapshot): SessionInfo[] {
  const raced = (session: SessionInfo): boolean =>
    tombstoneRowIds(session).some(id => sessionRemovalIntersected(snapshot, id))

  const kept = sessions.filter(session => !raced(session))

  return kept.length === sessions.length ? sessions : kept
}

// Rows a session refresh must preserve even if the aggregator omits them:
// in-flight first turns (message_count 0), pinned rows aged off the page, the
// actively-viewed chat (its "working" flag clears a beat before the aggregator
// sees the persisted row), and sessions whose turn just settled (same race, but
// for a chat the user has already navigated away from). Pass `scope` to only
// keep the active row when it belongs to the profile being paged.
function sessionsToKeep(scope?: string): Set<string> {
  const keep = new Set<string>([
    ...$workingSessionIds.get(),
    ...$pinnedSessionIds.get(),
    ...getRecentlySettledSessionIds()
  ])

  // Open tiles are user-visible state exactly like the selected row: a branch
  // child is a DRAFT until its first real turn, so the aggregator can't return
  // it — without this the next background refresh silently dropped the
  // optimistic `draft: branch #N` row while its tab was open, and the sidebar
  // showed no trace of the branch until first send.
  for (const tile of $sessionTiles.get()) {
    keep.add(tile.storedSessionId)
  }

  const active = $selectedStoredSessionId.get()

  if (active) {
    const session = scope ? $sessions.get().find(s => s.id === active) : null

    if (!scope || !session || normalizeProfileKey(session.profile) === scope) {
      keep.add(active)
    }
  }

  return keep
}

interface UseSessionListActionsArgs {
  profileScope: string
}

/** Owns the sidebar's session-list fetching + paging: recents, cron runs/jobs,
 *  and cron jobs. Returns the callbacks the controller
 *  wires into the sidebar and refresh effects. */
export function useSessionListActions({ profileScope }: UseSessionListActionsArgs) {
  const profileScopeRef = useRef(profileScope)
  const refreshSessionsRequestRef = useRef(0)

  useLayoutEffect(() => {
    profileScopeRef.current = profileScope
  }, [profileScope])

  /** Refresh cron jobs only while the profile that requested them remains active. */
  const refreshCronJobs = useCallback(async () => {
    const sessionProfile = sidebarProfileForScope(profileScope)

    if (sidebarProfileForScope(profileScopeRef.current) !== sessionProfile) {
      return
    }

    try {
      await refreshCronJobsStore(sessionProfile)
    } catch {
      // Non-fatal: the cron section just keeps its last-known jobs.
    }
  }, [profileScope])

  /** Refresh every sidebar session slice without committing an obsolete profile response. */
  const refreshSessions = useCallback(
    async (shouldPublish: () => boolean = () => true) => {
      const sessionProfile = sidebarProfileForScope(profileScope)

      if (!shouldPublish() || sidebarProfileForScope(profileScopeRef.current) !== sessionProfile) {
        return
      }

      const requestId = refreshSessionsRequestRef.current + 1
      refreshSessionsRequestRef.current = requestId
      // The loading flag exists to drive the initial skeletons (they only render
      // while the list is empty). Turn-complete / reconnect refreshes over a
      // populated list used to flip it true→false anyway, churning every
      // $sessionsLoading subscriber twice per turn for no visible change.
      const showLoading = $sessions.get().length === 0

      if (showLoading && shouldPublish()) {
        setSessionsLoadError(false)
        setSessionsLoading(true)
      }

      const owns = () =>
        shouldPublish() &&
        refreshSessionsRequestRef.current === requestId &&
        sidebarProfileForScope(profileScopeRef.current) === sessionProfile

      // Snapshot the removal lifecycle BEFORE the first read: a page read
      // pre-archive-commit can land post-prune, and only the generation
      // delta (not tombstone membership) still names the doomed row then.
      const removalSnapshot = captureSessionTombstoneGenerations()

      try {
        const limit = $sessionsLimit.get()

        // Require at least one message so abandoned/empty "Untitled" drafts (one
        // was created per TUI/desktop launch before the lazy-create fix) don't
        // clutter the sidebar.
        // Unified cross-profile list (served read-only off each profile's
        // state.db; no per-profile backend is spawned). Single-profile users get
        // the same rows tagged profile="default".
        // Scope every sidebar slice to the active profile (not always 'all') so a profile
        // with few recent sessions isn't windowed out of the cross-profile
        // recency page and never inherits another profile's cron section. ALL_PROFILES remains the explicit unified view.
        // Batched: one request opens each profile DB once and returns both
        // source-scoped slices, instead of separate listAllProfileSessions
        // calls that each reopened + re-counted every profile DB per refresh.
        const fetchPage = () =>
          listSidebarSessions({
            recentsProfile: sessionProfile,
            recentsLimit: limit,
            recentsExclude: SIDEBAR_EXCLUDED_SOURCES,
            cronLimit: CRON_SECTION_LIMIT
          })

        let activationEpoch = gatewayActivationEpoch()
        let result = await fetchPage()

        // A gateway activation that landed mid-request voids this page: it may
        // describe the source the window just left. But every activation bumps
        // the epoch, including a re-activation of the route already in front
        // (a resume or profile click through ensureGatewayAgent), and those
        // move no route atom, so no effect asks for the list again. Dropping
        // the page there left the sidebar on "No sessions" while the backend
        // held the rows (#67600). Still the newest refresh for this scope, so
        // re-read under the current epoch instead.
        while (owns() && gatewayActivationEpoch() !== activationEpoch) {
          activationEpoch = gatewayActivationEpoch()
          result = await fetchPage()
        }

        if (owns()) {
          const recents = result.recents
          const recentsErrors = recents.errors ?? result.errors

          const scopedRetry =
            recents.retry === true ||
            (sessionProfile !== 'all' && recents.profiles_failed?.[sessionProfile]?.retry === true) ||
            result.profiles_failed?.[sessionProfile]?.retry === true

          setCorruptSessionStores(result.storage)
          // A damaged store already has its own notice; Retry can't repair it.
          const retryableErrors = recentsErrors?.filter(e => !result.storage?.[e.profile])
          setSessionsLoadError(
            Boolean(showLoading && (scopedRetry || retryableErrors?.length) && (recents.sessions?.length ?? 0) === 0)
          )
          setSessionProfilesFailed(recentsErrors)

          // Drop rows the user just deleted/archived: a refresh can race an
          // in-flight mutation and the backend page still carries the doomed row.
          // Honoring the optimistic tombstone keeps the removal from flashing back
          // (the tombstone self-clears once projects.tree confirms the delete).
          // Signature-gate the swap (same pattern as cron): a refresh
          // that returns content-identical rows must keep the previous array
          // identity, or every sidebar memo keyed on $sessions recomputes and the
          // whole list re-renders once per turn/broadcast for nothing.
          setSessions(prev => {
            const incoming = dropTombstoned(
              carryForwardFailedProfileSessions(prev, recents.sessions ?? [], recents.errors ?? result.errors)
            )

            // Filter AFTER the merge: the guard must also catch survivors
            // (a stale previous slice can still hold the doomed row through
            // the keep set once the tombstone prune has cleared membership).
            const next = dropRemovalRaced(mergeSessionPage(prev, incoming, sessionsToKeep()), removalSnapshot)

            return sameCronSignature(prev, next) ? prev : next
          })
          // "Is there another page?" instead of an exact total: the backend
          // reports which profiles filled their window, which costs nothing on
          // top of the rows it already read (the old exact totals ran a COUNT(*)
          // per profile DB on every refresh). Reference-stable when unchanged so
          // the sidebar's group memos don't recompute per refresh.
          setSessionProfilesTruncated(prev => {
            const next = keepFailedProfileMeta(prev, recents.profiles_truncated ?? {}, recentsErrors)
            const prevKeys = Object.keys(prev)

            return prevKeys.length === Object.keys(next).length && prevKeys.every(key => prev[key] === next[key])
              ? prev
              : next
          })
          // Same identity gate: these totals only move when a session bills, and
          // a fresh object every refresh would repaint every profile header.
          setSessionProfilesUsage(prev => {
            const next = keepFailedProfileMeta(prev, recents.profiles_usage ?? {}, recentsErrors)
            const prevKeys = Object.keys(prev)

            return prevKeys.length === Object.keys(next).length &&
              prevKeys.every(
                key => prev[key]?.tokens === next[key]?.tokens && prev[key]?.cost_usd === next[key]?.cost_usd
              )
              ? prev
              : next
          })

          // Cron section: latest N cron sessions (kept so a pinned cron run still
          // resolves via sessionByAnyId), signature-gated like above. The
          // optimistic tombstone applies here too — the batched page can carry
          // a cron run whose delete/archive RPC is still in flight (#50928).
          setCronSessions(prev => {
            const incoming = carryForwardFailedProfileSessions(
              prev,
              dropRemovalRaced(dropTombstoned(result.cron.sessions ?? []), removalSnapshot),
              result.cron.errors ?? result.errors
            )

            return sameCronSignature(prev, incoming) ? prev : incoming
          })


        }
      } catch (error) {
        if (owns() && showLoading) {
          setSessionsLoadError(true)
        }

        throw error
      } finally {
        // Request identity preserves the zero-argument refresh contract across a
        // failed activation epoch; an explicit owner predicate is stronger and
        // must never release a newer switch's loading barrier.
        if (showLoading && shouldPublish() && refreshSessionsRequestRef.current === requestId) {
          setSessionsLoading(false)
        }
      }

      // Cron *jobs* are a distinct API (getCronJobs), not a session slice.
      if (shouldPublish() && sidebarProfileForScope(profileScopeRef.current) === sessionProfile) {
        void refreshCronJobs()
      }
    },
    [profileScope, refreshCronJobs]
  )

  const loadMoreSessions = useCallback(async () => {
    bumpSessionsLimit()
    await refreshSessions()
  }, [refreshSessions])

  // A filter searches the loaded page, so switching one on has to deepen the
  // page — otherwise "merged PRs" answers for the last 50 rows and reads as
  // "you only have 6 merged PRs". Clearing the filters hands the window back:
  // the list refreshes on every settled turn, and paying for 300 rows a turn
  // once the view is unfiltered again buys nothing. Whatever the user had
  // paged to by hand is what it returns to.
  const unfilteredLimit = useRef<null | number>(null)

  useEffect(
    () =>
      $sidebarFiltersActive.subscribe(active => {
        if (active) {
          unfilteredLimit.current ??= $sessionsLimit.get()

          if (raiseSessionsLimit(SIDEBAR_FILTERED_PAGE_SIZE)) {
            void refreshSessions()
          }
        } else if (unfilteredLimit.current !== null) {
          const restored = unfilteredLimit.current
          unfilteredLimit.current = null

          if ($sessionsLimit.get() > restored) {
            $sessionsLimit.set(restored)
            void refreshSessions()
          }
        }
      }),
    [refreshSessions]
  )

  return {
    loadMoreSessions,
    refreshCronJobs,
    refreshSessions
  }
}
