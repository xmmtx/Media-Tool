{
  description = "A powerful and versatile bulk file renaming tool built with PyQt5";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};

        pythonEnv = pkgs.python3.withPackages (ps: with ps; [
          pyqt5
          mutagen
        ]);

        ynrename = pkgs.stdenv.mkDerivation {
          pname = "ynrename";
          version = "1.0.0";

          src = ./.;

          nativeBuildInputs = [
            pkgs.libsForQt5.wrapQtAppsHook
            pkgs.imagemagick
          ];

          buildInputs = [
            pkgs.libsForQt5.qtbase
            pythonEnv
          ];

          installPhase = ''
            runHook preInstall

            mkdir -p $out/share/ynrename
            cp main.py $out/share/ynrename/

            mkdir -p $out/share/pixmaps
            convert "ynrename.ico[$(identify -format '%w %p\n' ynrename.ico | sort -rn | head -n1 | awk '{print $2}')]" $out/share/pixmaps/ynrename.png

            mkdir -p $out/share/applications
            cat > $out/share/applications/ynrename.desktop <<EOF
[Desktop Entry]
Name=YNRename
Comment=A powerful and versatile bulk file renaming tool built with PyQt5
Exec=ynrename
Icon=$out/share/pixmaps/ynrename.png
Type=Application
Categories=Utility;FileTools;
Terminal=false
EOF

            mkdir -p $out/bin
            makeWrapper ${pythonEnv}/bin/python3 $out/bin/ynrename \
              --add-flags "$out/share/ynrename/main.py"

            runHook postInstall
          '';

          postFixup = ''
            wrapQtApp "$out/bin/ynrename"
          '';

          meta = with pkgs.lib; {
            description = "A multi-functional utility designed to rename large batches of files quickly and efficiently. It offers a wide range of renaming rules from simple find-and-replace to advanced metadata-based formatting and regular expressions.";
            license = licenses.gpl3Only;
            platforms = platforms.all;
            mainProgram = "ynrename";
          };
        };
      in
      {
        packages.default = ynrename;
        packages.ynrename = ynrename;

        devShells.default = pkgs.mkShell {
          buildInputs = [ pythonEnv ];
          shellHook = ''
            echo "YNRename development environment ready."
            echo "Run with: python main.py"
          '';
        };
      });
}
