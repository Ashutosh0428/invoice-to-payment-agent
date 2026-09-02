"""Turn a native sample PDF into one that looks like it came off an office scanner.

The generated invoices in samples/inbox carry a clean text layer, so a parser can read them
without ever touching OCR. That makes them useless for proving the OCR path works, and OCR is
the path most real accounts-payable volume actually takes.

This rasterises a page, degrades it the way a scan degrades - a fraction of a degree of skew,
sensor noise, softened edges, the grey cast of a document scanned on default settings - and
writes it back as a PDF holding nothing but that image. The result has no text layer at all,
so Docling has to OCR it.

    python scripts/make_scanned_samples.py                     # every invoice in samples/inbox
    python scripts/make_scanned_samples.py INV-2026-0873_northwind.pdf
    python scripts/make_scanned_samples.py --dpi 200 --seed 7
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import fitz
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "samples" / "inbox"
SUFFIX = "_scanned"


def rasterise(pdf_path: Path, dpi: int) -> Image.Image:
    with fitz.open(pdf_path) as document:
        page = document[0]
        pixmap = page.get_pixmap(dpi=dpi)
        return Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)


def degrade(image: Image.Image, rng: random.Random) -> Image.Image:
    """Skew, noise, softness and a grey cast - the four things that break naive text extraction."""
    skew = rng.uniform(-0.9, 0.9)
    image = image.rotate(skew, resample=Image.BICUBIC, expand=False, fillcolor=(255, 255, 255))

    image = image.filter(ImageFilter.GaussianBlur(radius=rng.uniform(0.3, 0.6)))

    array = np.asarray(image).astype(np.int16)
    noise = np.random.default_rng(rng.randrange(2**32)).normal(0, 6.5, array.shape)
    array = np.clip(array + noise, 0, 255).astype(np.uint8)
    image = Image.fromarray(array)

    # Scanners rarely return a true white; the paper comes back a few points down.
    image = ImageEnhance.Brightness(image).enhance(rng.uniform(0.94, 0.98))
    image = ImageEnhance.Contrast(image).enhance(rng.uniform(0.88, 0.96))

    return image


def write_image_pdf(image: Image.Image, target: Path, dpi: int) -> None:
    """A PDF whose only content is the page image. No text layer, so OCR is the only way in."""
    image.convert("RGB").save(target, "PDF", resolution=float(dpi))


def copy_sidecar(source: Path, target: Path) -> None:
    sidecar = source.with_suffix(source.suffix + ".meta.json")
    if not sidecar.exists():
        return
    meta = json.loads(sidecar.read_text())
    meta["attachment"] = target.name
    subject = meta.get("subject", "")
    if subject:
        meta["subject"] = f"{subject} (scanned copy)"
    target.with_suffix(target.suffix + ".meta.json").write_text(json.dumps(meta, indent=2) + "\n")


def convert(source: Path, dpi: int, rng: random.Random) -> Path:
    target = source.with_name(f"{source.stem}{SUFFIX}.pdf")
    write_image_pdf(degrade(rasterise(source, dpi), rng), target, dpi)
    copy_sidecar(source, target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "files",
        nargs="*",
        help="filenames inside samples/inbox; defaults to every invoice " "not already scanned",
    )
    parser.add_argument("--dpi", type=int, default=200, help="rasterisation DPI (default 200)")
    parser.add_argument("--seed", type=int, default=11, help="seed for the degradation")
    args = parser.parse_args()

    rng = random.Random(args.seed)

    if args.files:
        sources = [INBOX / name for name in args.files]
    else:
        sources = sorted(path for path in INBOX.glob("INV-*.pdf") if not path.stem.endswith(SUFFIX))

    if not sources:
        parser.error(f"no source PDFs found in {INBOX}")

    for source in sources:
        if not source.exists():
            parser.error(f"{source} does not exist")
        target = convert(source, args.dpi, rng)
        size_kb = target.stat().st_size / 1024
        print(
            f"  {source.name}  ->  {target.name}  ({size_kb:.0f} KB, {args.dpi} dpi, no text layer)"
        )

    print(f"\n{len(sources)} scanned copies written to {INBOX.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
