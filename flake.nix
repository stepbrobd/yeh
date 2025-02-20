{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
    parts.url = "github:hercules-ci/flake-parts";
    parts.inputs.nixpkgs-lib.follows = "nixpkgs";
    systems.url = "github:nix-systems/default";
  };

  outputs = inputs: inputs.parts.lib.mkFlake { inherit inputs; } {
    systems = import inputs.systems;

    perSystem = { pkgs, ... }: {
      _module.args.lib = with inputs; builtins // nixpkgs.lib // parts.lib;

      devShells.default = pkgs.mkShell {
        packages = with pkgs; [
          dune_3
          ocaml
          ocamlformat
          sops
        ] ++ (with ocamlPackages; [
          core
          lambdasoup
          ocaml-print-intf
          odoc
          uri
          utop
        ]);
      };

      formatter = pkgs.writeShellScriptBin "formatter" ''
        ${pkgs.dune_3}/bin/dune fmt
        ${pkgs.nixpkgs-fmt}/bin/nixpkgs-fmt .
      '';
    };
  };
}
