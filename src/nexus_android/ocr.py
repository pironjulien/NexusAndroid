"""Lecture visuelle de l'écran quand l'arbre d'éléments est vide (jeux, applications Flutter ou dessinées à la main) :
reconnaissance de texte locale, sur le PC, par le moteur OCR intégré à Windows (Windows.Media.Ocr, langues installées
de l'utilisateur), sans service en ligne ni dépendance. Rendu : lignes de texte avec leur rectangle, en pixels de
l'écran, utilisables comme cibles. Le moteur se lance par Windows PowerShell 5.1 (le seul qui expose WinRT), sans
fenêtre ; absent (autre système), la lecture visuelle est simplement indisponible."""

from __future__ import annotations

import base64
import difflib
import json
import os
import shutil
import subprocess
import tempfile
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from PIL import Image as PILImage

TIMEOUT = 30.0
SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$type) {
    $task = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($env:NEXUS_OCR_IMAGE)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { throw 'no OCR language installed' }
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$lines = foreach ($line in $result.Lines) {
    $r = $line.Words | ForEach-Object { $_.BoundingRect }
    $x1 = ($r | Measure-Object X -Minimum).Minimum; $y1 = ($r | Measure-Object Y -Minimum).Minimum
    $x2 = ($r | ForEach-Object { $_.X + $_.Width } | Measure-Object -Maximum).Maximum
    $y2 = ($r | ForEach-Object { $_.Y + $_.Height } | Measure-Object -Maximum).Maximum
    [pscustomobject]@{ text = $line.Text; box = @([int]$x1, [int]$y1, [int]$x2, [int]$y2) } }
$stream.Dispose()
[Console]::OutputEncoding = [Text.Encoding]::UTF8
ConvertTo-Json -Compress -Depth 4 @($lines)
"""


ENCODED = base64.b64encode(SCRIPT.encode("utf-16-le")).decode()  # bloc entier, sans fichier ni lecture ligne à ligne


@dataclass
class Line:
    text: str
    box: tuple[int, int, int, int]  # pixels de l'écran

    @property
    def center(self) -> tuple[int, int]:
        return (self.box[0] + self.box[2]) // 2, (self.box[1] + self.box[3]) // 2


def available() -> bool:
    return os.name == "nt" and shutil.which("powershell.exe") is not None


def parse(out: str, factor: float) -> list[Line]:
    """Sortie JSON du script → lignes ; factor : pixels de l'écran par pixel de l'image lue."""
    data = json.loads(out or "[]")
    if isinstance(data, dict):
        data = [data]
    lines = []
    for item in data:
        text = str(item.get("text", "")).strip()
        box = item.get("box") or [0, 0, 0, 0]
        if text and len(box) == 4:
            x1, y1, x2, y2 = (round(v * factor) for v in box)
            lines.append(Line(text, (x1, y1, x2, y2)))
    return lines


class OcrError(RuntimeError):
    """Lecture impossible (moteur absent, script en échec, délai dépassé) : distincte d'une image lue sans texte."""


def read(image: PILImage.Image, factor: float = 1.0) -> list[Line]:
    """Texte de l'image (PNG écrit dans un dossier temporaire, effacé aussitôt) ; [] : image lue, aucun texte.
    OcrError si le moteur manque ou échoue (jamais pris pour « aucun code » par le masquage)."""
    if not available():
        raise OcrError("Windows OCR engine unavailable")
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        if max(image.size) > 4000:  # MaxImageDimension du moteur : 10 000 ; au-delà de 4000, aucun gain de lecture
            scale = 4000 / max(image.size)
            image = image.resize((round(image.width * scale), round(image.height * scale)))
            factor /= scale
        image.convert("RGB").save(path, "PNG")
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand",
             ENCODED], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT,
            env={**os.environ, "NEXUS_OCR_IMAGE": path}, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise OcrError((result.stderr or "OCR script failed").strip()[:200])
        return parse(result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "[]", factor)
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        raise OcrError(str(e)[:200] or type(e).__name__) from None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


SIMILAR = 0.75  # ressemblance minimale entre le texte cherché et un morceau de ligne lue (OCR imparfait sur une photo)


def _letters(text: str) -> str:
    """Lettres et chiffres seuls, sans accents ni casse : « Champ-de-Mars » → « champdemars »."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return "".join(c for c in plain.lower() if c.isalnum())


def closest[T](wanted: str, elements: Sequence[T]) -> T | None:
    """Élément OCR dont un morceau ressemble le plus au texte cherché (« *dhamp7de-Mars » pour « Champ-de-Mars »),
    au moins SIMILAR ; None sinon. Pour du texte lu sur l'image seulement : l'arbre, lui, est exact."""
    target = _letters(wanted)
    if len(target) < 3:
        return None
    best: T | None = None
    score = SIMILAR
    for e in elements:
        line = _letters(getattr(e, "text", ""))
        n = len(target)
        windows = [line[i:i + m] for m in (n - 1, n, n + 1) for i in range(0, max(1, len(line) - m + 1))] or [line]
        ratio = max(difflib.SequenceMatcher(None, target, w).ratio() for w in windows)
        if ratio > score:
            best, score = e, ratio
    return best
