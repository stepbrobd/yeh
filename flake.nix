{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
    parts.url = "github:hercules-ci/flake-parts";
    parts.inputs.nixpkgs-lib.follows = "nixpkgs";
    systems.url = "github:nix-systems/default";
  };

  outputs = inputs: inputs.parts.lib.mkFlake { inherit inputs; } {
    systems = import inputs.systems;

    perSystem = { pkgs, system, ... }: {
      _module.args = {
        lib = with inputs; builtins // nixpkgs.lib // parts.lib;
        pkgs = import inputs.nixpkgs {
          inherit system;
          overlays = [
            (final: prev: {
              ocamlPackages = prev.ocamlPackages.overrideScope (_: prev': {
                mirage-crypto-rng = prev'.mirage-crypto-rng.overrideAttrs (_: {
                  # https://github.com/mirage/mirage-crypto/issues/216
                  # https://github.com/nixos/nixpkgs/pull/356634
                  doCheck = !(with final.stdenv; isDarwin && isAarch64);
                });
              });
            })
          ];
        };
      };

      devShells.default = pkgs.mkShell {
        packages = with pkgs; [
          dune_3
          ocaml
          ocamlformat
          sops
        ] ++ (with ocamlPackages; [
          cohttp
          cohttp-lwt
          cohttp-lwt-unix
          core
          lambdasoup
          lwt
          ocaml-print-intf
          odoc
          ptime
          uri
          utop
        ]);
      };

      formatter = pkgs.writeShellScriptBin "formatter" ''
        ${pkgs.deno}/bin/deno fmt readme.md
        ${pkgs.dune_3}/bin/dune fmt
        ${pkgs.nixpkgs-fmt}/bin/nixpkgs-fmt .
      '';
    };
  };
}
