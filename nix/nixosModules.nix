# nix/nixosModules.nix — the NixOS module for hermes-agent
#
# This module shares its options, its renderers for config.yaml, .env and
# documents, and its state setup with the Home Manager module
# (nix/homeManagerModules.nix). The shared code is in nix/moduleCommon.nix.
# This file holds only the parts that need root: the service user, a system
# state directory, the system PATH, and the native systemd service.
#
# Usage:
#   services.hermes-agent = {
#     enable = true;
#     settings.model.default = "anthropic/claude-sonnet-4";
#     environmentFiles = [ config.sops.secrets."hermes/env".path ];
#   };
#
{ inputs, ... }:
{
  flake.nixosModules.default =
    {
      config,
      lib,
      options,
      pkgs,
      ...
    }:

    let
      cfg = config.services.hermes-agent;
      common = import ./moduleCommon.nix { inherit lib; };

      effectivePackage = common.effectivePackage cfg;
      hermes-agent = inputs.self.packages.${pkgs.stdenv.hostPlatform.system}.default;

      hermesHome = "${cfg.stateDir}/.hermes";

      # config.yaml mode: group-writable (0660) when interactive users share this
      # HERMES_HOME via addToSystemPackages, so they can save settings through the
      # CLI/TUI without hitting EACCES; otherwise group-read-only (0640). Secrets
      # (.env) stay 0640 regardless.
      configYamlMode = if cfg.addToSystemPackages then "0660" else "0640";

      # The hardening and the environment for the gateway unit.
      commonServiceConfig = {
        User = cfg.user;
        Group = cfg.group;
        WorkingDirectory = cfg.workingDirectory;

        Restart = cfg.restart;
        RestartSec = cfg.restartSec;

        # Shared-state: files created by the service should be group-writable
        # so interactive users in the hermes group can read/write them.
        UMask = "0007";

        # Hardening
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = false;
        ReadWritePaths = [
          cfg.stateDir
          cfg.workingDirectory
        ];
        PrivateTmp = true;
      };

      commonUnitEnvironment = {
        HOME = cfg.stateDir;
      }
      // common.processEnvironment { inherit hermesHome; };

      unitPath = common.processPath { inherit pkgs cfg; };


    in
    {
      options.services.hermes-agent =
        (common.sharedOptions {
          defaultPackage = hermes-agent;
          defaultPackageText = lib.literalExpression "hermes-agent.packages.\${system}.default";
          defaultWorkingDirectory = "${cfg.stateDir}/workspace";
          defaultWorkingDirectoryText = lib.literalExpression ''"''${cfg.stateDir}/workspace"'';
        })
        // (
          with lib;
          {
            # ── Service identity ───────────────────────────────────────────
            user = mkOption {
              type = types.str;
              default = "hermes";
              description = "System user running the gateway.";
            };

            group = mkOption {
              type = types.str;
              default = "hermes";
              description = "System group running the gateway.";
            };

            createUser = mkOption {
              type = types.bool;
              default = true;
              description = "Create the user/group automatically.";
            };

            # ── Directories ────────────────────────────────────────────────
            stateDir = mkOption {
              type = types.str;
              default = "/var/lib/hermes";
              description = "State directory. Contains .hermes/ subdir (HERMES_HOME).";
            };

            addToSystemPackages = mkOption {
              type = types.bool;
              default = false;
              description = ''
                Add the hermes CLI to environment.systemPackages and export
                HERMES_HOME system-wide (via environment.variables) so interactive
                shells share state with the gateway service.
              '';
            };


          }
        );

      config = lib.mkIf cfg.enable (
        lib.mkMerge [

          # ── Merge MCP servers into settings ────────────────────────────────
          (lib.mkIf (cfg.mcpServers != { }) {
            services.hermes-agent.settings.mcp_servers = common.mcpServersToConfig cfg.mcpServers;
          })

          # ── User / group ──────────────────────────────────────────────────
          (lib.mkIf cfg.createUser {
            users.groups.${cfg.group} = { };
            users.users.${cfg.user} = {
              isSystemUser = true;
              group = cfg.group;
              home = cfg.stateDir;
              createHome = true;
              shell = pkgs.bashInteractive;


            };
          })

          # ── Host CLI ──────────────────────────────────────────────────────
          # Add the hermes CLI to system PATH and export HERMES_HOME system-wide
          # so interactive shells share state (sessions and skills) with the
          # gateway service instead of creating a separate ~/.hermes/.
          (lib.mkIf cfg.addToSystemPackages {
            environment.systemPackages = [ effectivePackage ];
            environment.variables.HERMES_HOME = hermesHome;
          })


          # ── Assertions ─────────────────────────────────────────────────────
          {
            assertions =
              common.pluginNameAssertions {
                inherit cfg;
                optionPath = "services.hermes-agent";
              }
              ++ common.workspaceFilesAssertions {
                inherit cfg;
                opt = options.services.hermes-agent.workingDirectory;
                optionPath = "services.hermes-agent";
              }
              ;
          }

          # ── Per-user profile for extraPackages ───────────────────────────
          # Wire extraPackages into the hermes user's per-user profile so the
          # login-shell snapshot (which rebuilds PATH from NixOS profiles) sees
          # them.  The systemd service PATH also includes them for direct access.
          (lib.mkIf (cfg.extraPackages != [ ]) {
            # listOf options are merged by the NixOS module system — this appends to
            # any packages the operator assigned to this user externally (e.g. when
            # createUser = false and the user definition lives elsewhere in the config).
            users.users.${cfg.user}.packages = cfg.extraPackages;
          })


          # ── Directories ───────────────────────────────────────────────────
          {
            systemd.tmpfiles.rules = [
              "d ${cfg.stateDir}                2770 ${cfg.user} ${cfg.group} - -"
              "d ${hermesHome}                  2770 ${cfg.user} ${cfg.group} - -"
              "d ${cfg.stateDir}/home           0750 ${cfg.user} ${cfg.group} - -"
              "d ${cfg.workingDirectory}        2770 ${cfg.user} ${cfg.group} - -"
            ]
            ++ map (d: "d ${hermesHome}/${d} 2770 ${cfg.user} ${cfg.group} - -") common.stateSubdirs;
          }

          # ── Activation: link config + auth + documents ────────────────────
          {
            system.activationScripts."hermes-agent-setup" =
              lib.stringAfter
                (
                  [ "users" ] ++ lib.optional (config.system.activationScripts ? setupSecrets) "setupSecrets"
                )
                ''
                  # Ensure directories exist (activation runs before tmpfiles)
                  mkdir -p ${hermesHome}
                  mkdir -p ${cfg.stateDir}/home
                  mkdir -p ${cfg.workingDirectory}
                  chown ${cfg.user}:${cfg.group} ${cfg.stateDir} ${hermesHome} ${cfg.stateDir}/home ${cfg.workingDirectory}
                  chmod 2770 ${cfg.stateDir} ${hermesHome} ${cfg.workingDirectory}
                  chmod 0750 ${cfg.stateDir}/home

                  # Create subdirs, set setgid + group-writable, migrate existing files.
                  # Nix-managed .env/.managed stay 0640/0644; config.yaml uses
                  # configYamlMode (0660 under addToSystemPackages, else 0640).
                  find ${hermesHome} -maxdepth 1 \
                    \( -name "*.db" -o -name "*.db-wal" -o -name "*.db-shm" -o -name "SOUL.md" \) \
                    -exec chmod g+rw {} + 2>/dev/null || true
                  for _subdir in ${lib.concatStringsSep " " common.stateSubdirs}; do
                    mkdir -p "${hermesHome}/$_subdir"
                    chown ${cfg.user}:${cfg.group} "${hermesHome}/$_subdir"
                    chmod 2770 "${hermesHome}/$_subdir"
                    find "${hermesHome}/$_subdir" -type f \
                      -exec chmod g+rw {} + 2>/dev/null || true
                  done

                  ${common.mkStateScript {
                    inherit pkgs cfg hermesHome;
                    workingDirectory = cfg.workingDirectory;
                    owner = "${cfg.user}:${cfg.group}";
                    stateDirs = common.stateSubdirs;
                    modes = {
                      config = configYamlMode;
                      env = "0640";
                      managed = "0644";
                      auth = "0600";
                      document = "0640";
                    };
                  }}

                  chown -h ${cfg.user}:${cfg.group} ${hermesHome}/plugins/nix-managed-* 2>/dev/null || true
                '';
          }

          {
            systemd.services.hermes-agent = {
              description = "Hermes Agent Gateway";
              wantedBy = [ "multi-user.target" ];
              after = [
                "network-online.target"
              ];
              wants = [
                "network-online.target"
              ];

              # cfg.environment and cfg.environmentFiles are written to
              # $HERMES_HOME/.env by the activation script. load_hermes_dotenv()
              # reads them at Python startup — no systemd EnvironmentFile needed.
              environment = commonUnitEnvironment;


              serviceConfig = commonServiceConfig // {
                ExecStart = lib.escapeShellArgs (common.gatewayArgv cfg);
              };

              path = unitPath;
            };
          }
        ]
      );
    };
}
