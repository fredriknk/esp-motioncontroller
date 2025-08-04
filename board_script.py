# build_outputs.py
# Python 3.9+
# Standardizes KiCad 9 project outputs on Windows (also works cross-platform).
# Outputs:
#   3D: 3D_MODEL/<proj>.step (+ optional .glb)
#   Renders: PICTURES/<proj>_{top|bottom|side[|iso]}.png
#   Docs: DOCUMENTATION/<proj>_schematic.pdf, <proj>_erc.rpt, <proj>_board_prints.pdf
#   Fab: PRODUCTION/<timestamp>_<proj>/{gerbers,drill,...} + ZIP, <proj>_{pos,bom}.csv
#
# Requires KiCad 9 `kicad-cli`. Optionally uses KiKit for vendor ZIP (--kikit jlcpcb, etc).

import argparse
import subprocess
import sys
import shutil
from pathlib import Path
from datetime import datetime
import zipfile

# ---------- Helpers ----------

def which_kicad_cli():
    # Prefer environment override
    env = Path(str(Path.cwd()))
    exe = shutil.which("kicad-cli")
    if exe:
        return exe
    # Windows default install path for KiCad 9
    win_default = Path(r"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe")
    if win_default.exists():
        return str(win_default)
    raise FileNotFoundError(
        "kicad-cli not found on PATH and not at default KiCad 9 location.\n"
        "Add KiCad to PATH or set KICAD_CLI env var / adjust this script."
    )

def run(cmd, cwd=None):
    print(">>", " ".join(map(str, cmd)))
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stdout)
        print(res.stderr, file=sys.stderr)
        raise RuntimeError(f"Command failed with code {res.returncode}")
    return res.stdout.strip()

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)
    return p

def timestamp_tag():
    return datetime.now().strftime("%Y%m%d_%H%M")

def project_paths(project_file: Path):
    """
    Accepts a .kicad_pro, base name, or path stem.
    Returns (proj_stem, sch_path, pcb_path).
    """
    if project_file.suffix.lower() == ".kicad_pro":
        stem = project_file.with_suffix("")  # same base name
        sch = stem.with_suffix(".kicad_sch")
        pcb = stem.with_suffix(".kicad_pcb")
    else:
        # if user passed without extension, try both
        stem = project_file
        sch = stem.with_suffix(".kicad_sch")
        pcb = stem.with_suffix(".kicad_pcb")
    if not sch.exists():
        raise FileNotFoundError(f"Schematic not found: {sch}")
    if not pcb.exists():
        raise FileNotFoundError(f"Board not found: {pcb}")
    return stem.name, sch, pcb

def zip_dir(src_dir: Path, zip_path: Path):
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in src_dir.rglob("*"):
            zf.write(p, arcname=p.relative_to(src_dir))

# ---------- Export steps ----------

def export_3d(kicad, pcb_path: Path, out_dir: Path, make_glb: bool):
    ensure_dir(out_dir)
    step_out = out_dir / f"{pcb_path.stem}.step"
    run([kicad, "pcb", "export", "step", "-o", str(step_out), str(pcb_path)])
    if make_glb:
        glb_out = out_dir / f"{pcb_path.stem}.glb"
        run([kicad, "pcb", "export", "glb", "-o", str(glb_out), str(pcb_path)])
    return step_out

def export_pictures(kicad, pcb_path: Path, out_dir: Path, iso: bool):
    ensure_dir(out_dir)
    top = out_dir / f"{pcb_path.stem}_top.png"
    bot = out_dir / f"{pcb_path.stem}_bottom.png"
    side = out_dir / f"{pcb_path.stem}_side.png"

    run([kicad, "pcb", "render", "-o", str(top), "--side", "top", "--background", "transparent", str(pcb_path)])
    run([kicad, "pcb", "render", "-o", str(bot), "--side", "bottom", "--background", "transparent", str(pcb_path)])
    # "side" = orthographic left view; change to 'right/front/back' if preferred
    run([kicad, "pcb", "render", "-o", str(side), "--side", "left", "--background", "transparent", str(pcb_path)])

    iso_out = None
    if iso:
        iso_out = out_dir / f"{pcb_path.stem}_iso.png"
        run([
            kicad, "pcb", "render", "-o", str(iso_out),
            "--background", "transparent", "--perspective",
            "--rotate", "-45,0,45", "--zoom", "1", str(pcb_path)
        ])
    return [top, bot, side] + ([iso_out] if iso_out else [])

def export_docs(kicad, sch_path: Path, pcb_path: Path, out_dir: Path, include_drc: bool):
    ensure_dir(out_dir)
    # Schematic PDF
    sch_pdf = out_dir / f"{sch_path.stem}_schematic.pdf"
    run([kicad, "sch", "export", "pdf", "-o", str(sch_pdf), str(sch_path)])

    # ERC report
    erc_rpt = out_dir / f"{sch_path.stem}_erc.rpt"
    run([kicad, "sch", "erc", "-o", str(erc_rpt), str(sch_path)])

    # Board prints PDF (multi-page: common layers)
    board_pdf = out_dir / f"{pcb_path.stem}_board_prints.pdf"
    layers = ",".join([
        "F.Cu","B.Cu","F.SilkS","B.SilkS",
        "F.Mask","B.Mask","Edge.Cuts","F.Fab","B.Fab","User.Drawings"
    ])
    run([
        kicad, "pcb", "export", "pdf",
        "-o", str(board_pdf),
        "--layers", layers,
        "--mode-multipage",
        str(pcb_path)
    ])

    # Optional DRC (report lives with docs so it’s easy to review)
    drc_rpt = None
    if include_drc:
        drc_rpt = out_dir / f"{pcb_path.stem}_drc.rpt"
        run([kicad, "pcb", "drc", "-o", str(drc_rpt), "--format", "report", str(pcb_path)])

    return sch_pdf, erc_rpt, board_pdf, drc_rpt

def export_fab(kicad, sch_path: Path, pcb_path: Path, out_dir: Path, zip_outputs: bool):
    """
    - Gerbers into PRODUCTION/<ts>_<proj>/gerbers
    - Drill into .../drill
    - POS/PNP CSV and BOM CSV into the same PRODUCTION folder root
    """
    root = ensure_dir(out_dir)
    gerb_dir = ensure_dir(root / "gerbers")
    drill_dir = ensure_dir(root / "drill")

    # Gerbers: honor project plot settings OR explicit layers.
    # Using saved board plot params makes this repeatable across machines.
    run([kicad, "pcb", "export", "gerbers", "-o", str(gerb_dir), "--board-plot-params", str(pcb_path)])

    # Drill (Excellon) + map
    run([kicad, "pcb", "export", "drill", "-o", str(drill_dir), "--format", "excellon", "--generate-map", str(pcb_path)])

    # POS/PNP (CSV, both sides, mm)
    pos_csv = root / f"{pcb_path.stem}_pos.csv"
    run([kicad, "pcb", "export", "pos", "-o", str(pos_csv), "--format", "csv", "--units", "mm", "--side", "both", str(pcb_path)])

    # BOM (CSV) – include common fields if present
    bom_csv = root / f"{sch_path.stem}_bom.csv"
    fields = "Reference,Value,Footprint,${QUANTITY},Manufacturer,MPN,Datasheet,${DNP}"
    labels = "Refs,Value,Footprint,Qty,Manufacturer,MPN,Datasheet,DNP"
    run([kicad, "sch", "export", "bom", "-o", str(bom_csv), "--fields", fields, "--labels", labels, "--group-by", "Value,Footprint,MPN", str(sch_path)])

    zip_path = None
    if zip_outputs:
        zip_path = root / f"{pcb_path.stem}_gerbers_{timestamp_tag()}.zip"
        zip_dir(gerb_dir, zip_path)
    return gerb_dir, drill_dir, pos_csv, bom_csv, zip_path

def run_kikit_fab(vendor: str, pcb_path: Path, out_dir: Path):
    """
    Optional: use KiKit to make a vendor-ready ZIP (e.g., JLCPCB).
    """
    kikit = shutil.which("kikit")
    if not kikit:
        raise FileNotFoundError("KiKit executable not found on PATH.")
    ensure_dir(out_dir)
    # Basic usage: kikit fab <vendor> BOARD OUTPUTDIR
    # KiKit writes gerbers.zip etc. See vendor docs for flags.
    run([kikit, "fab", vendor, str(pcb_path), str(out_dir)])
    return out_dir / "gerbers.zip"

# ---------- Main ----------

def main():
    parser = argparse.ArgumentParser(description="Standardize KiCad 9 outputs into your folder structure.")
    parser.add_argument("--project", required=True, help="Path to .kicad_pro (or base path) of the project.")
    parser.add_argument("--root", default=".", help="Repo root containing 3D_MODEL, PICTURES, DOCUMENTATION, PRODUCTION.")
    parser.add_argument("--prod-dir", default="PRODUCTION", help="Production folder relative to root (default: PRODUCTION).")
    parser.add_argument("--iso", action="store_true", help="Also render an isometric image.")
    parser.add_argument("--glb", action="store_true", help="Also export .glb 3D model.")
    parser.add_argument("--zip", action="store_true", help="Zip gerbers into <proj>_gerbers_<timestamp>.zip.")
    parser.add_argument("--kikit", default=None, help="Optional: vendor for KiKit 'fab' (e.g., 'jlcpcb').")
    parser.add_argument("--skip-drc", action="store_true", help="Skip DRC report.")
    args = parser.parse_args()

    kicad = which_kicad_cli()
    proj_stem, sch_path, pcb_path = project_paths(Path(args.project))

    root = Path(args.root).resolve()
    three_d_dir = ensure_dir(root / "3D_MODEL")
    pics_dir = ensure_dir(root / "PICTURES")
    docs_dir = ensure_dir(root / "DOCUMENTATION")

    # Timestamped production run folder to keep history
    prod_root = ensure_dir(root / args.prod_dir / f"{timestamp_tag()}_{proj_stem}")

    print(f"Project: {proj_stem}")
    print(f"SCH:     {sch_path}")
    print(f"PCB:     {pcb_path}")
    print(f"Root:    {root}")

    # 1) 3D model(s)
    export_3d(kicad, pcb_path, three_d_dir, args.glb)

    # 2) Renders
    export_pictures(kicad, pcb_path, pics_dir, args.iso)

    # 3) Documentation (schematic PDF, ERC, board prints PDF [+ optional DRC])
    export_docs(kicad, sch_path, pcb_path, docs_dir, include_drc=not args.skip_drc)

    # 4) Fabrication (Gerbers, drill, PNP, BOM [+ ZIP])
    export_fab(kicad, sch_path, pcb_path, prod_root, zip_outputs=args.zip)

    # 5) Optional vendor-specific fab package via KiKit (e.g., jlcpcb)
    if args.kikit:
        print(f"Running KiKit fab for vendor: {args.kikit}")
        vendor_zip = run_kikit_fab(args.kikit, pcb_path, prod_root)
        print(f"KiKit vendor ZIP: {vendor_zip}")

    print("\nAll done ✅")
    print(f"- 3D models:       {three_d_dir}")
    print(f"- Pictures:        {pics_dir}")
    print(f"- Documentation:   {docs_dir}")
    print(f"- Production run:  {prod_root}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)
