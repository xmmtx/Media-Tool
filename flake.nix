{
  description = "Multi-functional Media Manager & Batch Renamer (PyQt6)";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        pythonEnv = pkgs.python3.withPackages (ps: with ps; [
          pyqt6
          mutagen
        ]);

        mediaTool = pkgs.stdenv.mkDerivation {
          pname = "media-tool";
          version = "0.1.0";

          src = ./.;

          nativeBuildInputs = [
            pkgs.libsForQt6.wrapQtAppsHook
          ];

          buildInputs = [
            pkgs.libsForQt6.qtbase
            pkgs.ffmpeg # 提供 ffprobe（实测视频分辨率）
            pythonEnv
          ];

          installPhase = ''
            runHook preInstall

            mkdir -p $out/share/media-tool
            cp -r main.py src $out/share/media-tool/

            mkdir -p $out/bin
            makeWrapper ${pythonEnv}/bin/python3 $out/bin/media-tool \
              --add-flags "$out/share/media-tool/main.py"

            runHook postInstall
          '';

          postFixup = ''
            wrapQtApp "$out/bin/media-tool"
          '';

          meta = with pkgs.lib; {
            description = "Multi-functional media manager & batch renamer with movie/TV/music pipelines, TMDB and LLM integration.";
            license = licenses.gpl3Only;
            platforms = platforms.all;
            mainProgram = "media-tool";
          };
        };
      in
      {
        packages.default = mediaTool;
        packages.media-tool = mediaTool;

        devShells.default = pkgs.mkShell {
          buildInputs = [ pythonEnv pkgs.ffmpeg ];
          shellHook = ''
            echo "Media-Tool development environment ready."
            echo "Run with: python main.py"
          '';
        };
      });
}
