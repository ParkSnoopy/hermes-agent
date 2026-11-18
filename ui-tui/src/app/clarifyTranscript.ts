import { sessionEn } from '../copy/session.js'
import { buildToolTrailLine, clarifyAnswerText } from '../lib/text.js'

export interface ClarifyTranscriptQuestion {
  multiSelect?: boolean
  qid: string
  question: string
}

export const clarifyQuestionCountLabel = (count: number) =>
  (count === 1 ? sessionEn.session.main.clarifyQuestionsOne(String(count)) : sessionEn.session.main.clarifyQuestionsOther(String(count)))

/** The transcript a settled clarify leaves behind, as main's TUI wrote it: the collapsed tool row
 * `Clarify("N question(s)")` and one user bubble of `question → answer` lines (a blank answer reads
 * as skipped). The canonical one-card path and the batch path both settle through this. */
export function clarifySettledRows(questions: ClarifyTranscriptQuestion[], answers: Record<string, string | undefined>) {
  return {
    trail: buildToolTrailLine('clarify', clarifyQuestionCountLabel(questions.length)),
    userText: questions
      .map(
        q =>
          `${q.question} → ${answers[q.qid]?.trim() ? clarifyAnswerText(answers[q.qid]!, q.multiSelect) : sessionEn.session.main.skipped}`
      )
      .join('\n')
  }
}
