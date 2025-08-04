set "PROJECT=.\CAD\esp-motioncontroller\esp-motioncontroller"
set "VENDOR=jlcpcb"
set "PATH=C:\Program Files\KiCad\9.0\bin;C:\Program Files\KiCad\9.0\bin\Scripts;%PATH%"

echo "Generating outputs for %PROJECT% with vendor %VENDOR%"
python .\build_outputs.py --project %PROJECT%.kicad_pro --no-timestamp --iso --zip --kikit %VENDOR%
