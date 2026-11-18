# nix/python.nix — uv2nix virtual environment builder
{
  lib,
  callPackage,
  uv2nix,
  pyproject-nix,
  pyproject-build-systems,

  # Filtered Python source (see lib.nix pythonSrc) — keeps JS/docs/skills
  # edits from invalidating the venv derivation.
  pythonSrc,
  dependency-groups ? [ "all" ],
}:
let
  # The interpreter family comes from pm/lock.json (pythonLock.nix owns the
  # selection); every override below must be built for THAT interpreter.
  pythonLock = callPackage ./pythonLock.nix { };
  python = pythonLock.interpreter;


  workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = pythonSrc; };


  overlay = workspace.mkPyprojectOverlay {
    sourcePreference = "wheel";
  };


  # Legacy alibabacloud packages ship only sdists with setup.py/setup.cfg
  # and no pyproject.toml, so setuptools isn't declared as a build dep.
  buildSystemOverrides =
    final: prev:
    builtins.mapAttrs
      (
        name: _:
        prev.${name}.overrideAttrs (old: {
          nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [ final.setuptools ];
        })
      )
      (
        lib.genAttrs [
          "alibabacloud-credentials-api"
          "alibabacloud-endpoint-util"

          "alibabacloud-gateway-spi"
          "alibabacloud-tea"
        ] (_: null)
      );


  pythonSet =
    (callPackage pyproject-nix.build.packages {
      inherit python;
    }).overrideScope
      (
        lib.composeManyExtensions [
          pyproject-build-systems.overlays.default
          overlay
          buildSystemOverrides

          # ``setup.py`` permits wheel/sdist creation only from the sealed
          # Hermes derivation. This is deliberately a derivation environment
          # variable, not a devShell variable: ``nix develop -c uv build``
          # must remain blocked.
          (final: prev: {
            hermes-agent = prev.hermes-agent.overrideAttrs (_old: {
              HERMES_NIX_BUILD = "1";
            });
          })
        ]
      );

  # The editable venv points at the live checkout, so it uses an
  # UNFILTERED workspace rooted at a real path — mkEditablePyprojectOverlay
  # computes relative paths via lib.path.splitRoot, which rejects the
  # filtered pythonSrc (a cleanSourceWith set, not a path).  Filtering
  # buys nothing here anyway: the editable install reads from
  # $HERMES_PYTHON_SRC_ROOT at runtime.
  workspaceRoot = ./..;
  editableWorkspace = uv2nix.lib.workspace.loadWorkspace { inherit workspaceRoot; };
  editableOverlay = editableWorkspace.mkEditablePyprojectOverlay {
    root = "$HERMES_PYTHON_SRC_ROOT"; # resolved at shellHook time
  };

  editableSet = pythonSet.overrideScope (
    lib.composeManyExtensions [
      editableOverlay
      (final: prev: {
        hermes-agent = prev.hermes-agent.overrideAttrs (old: {
          # point straight at the real source instead of the filtered nix store copy
          src = workspaceRoot;
          nativeBuildInputs = old.nativeBuildInputs ++ final.resolveBuildSystem { editables = [ ]; };
        });
      })
    ]
  );
in
{
  inherit python;

  venv = pythonSet.mkVirtualEnv "hermes-agent-env" {
    hermes-agent = dependency-groups;
  };
  editableVenv = editableSet.mkVirtualEnv "hermes-agent-editable-env" {
    hermes-agent = dependency-groups;
  };
}
