#!/usr/bin/env python3
"""Génère un rapport chirp HTML auto-contenu depuis un répertoire (ou une liste) de logs blackbox.

Un log (.bbl/.bfl) = une passe. Les passes sont triées par nom de fichier ; la DERNIÈRE passe
devient la référence (primary) du rapport. Au-delà de MAX_OVERLAY_PASSES (8), seules les dernières
sont affichées.

Exemples :
    # tout un répertoire -> HTML à côté
    python scripts/gen_report.py tests/data/km -o docs/tune-sessions/km.html

    # fichiers explicites, dans l'ordre donné (le dernier = primary)
    python scripts/gen_report.py 08.bbl 09.bbl 12.bbl -o rapport.html

    # rapport en anglais, ordre inversé
    python scripts/gen_report.py logs/ --lang en --reverse -o out.html
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Permet de lancer le script sans installer le paquet (ajoute la racine du repo au path).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from betaflight_chirp_core import analyse_log, build_report, decode  # noqa: E402

EXTS = (".bbl", ".bfl")

# --- JSON d'indicateurs : on retire les DONNÉES DE GRAPH (longues courbes/heatmaps) et la NOTATION
# (tune_score), pour ne garder que les indicateurs scalaires (marges, Ms, step.metrics, équilibre PID,
# qualité de filtrage, budget de délai, pics de bruit, config…). ---
_JSON_DROP_KEYS = {
    "tune_score",                                   # notation (score/grade)
    "spectrogram", "throttle_map", "rpm_map",       # heatmaps = données de graph pures
    "gain_band", "phase_band", "coherence_band",    # bandes d'incertitude (courbes)
    "_glossary", "_strings",                         # i18n (inutile hors rendu)
}
_JSON_CURVE_LEN = 24   # toute liste plus longue = courbe (freq/gain/phase/step.y/PSD…) -> retirée


def _prune_indicators(obj):
    """Copie en retirant les longues courbes (listes > _JSON_CURVE_LEN) + les clés de graph/notation.

    Conserve les petites listes (paires *_range, pids, peaks…) qui sont des indicateurs."""
    if isinstance(obj, dict):
        return {k: _prune_indicators(v) for k, v in obj.items()
                if k not in _JSON_DROP_KEYS and not (isinstance(v, list) and len(v) > _JSON_CURVE_LEN)}
    if isinstance(obj, list):
        return [_prune_indicators(x) for x in obj]
    return obj


def collect_logs(inputs: list[str], reverse: bool) -> list[Path]:
    """Développe les entrées (répertoires -> leurs logs, fichiers -> tels quels) en liste triée.

    Un répertoire est trié par nom ; des fichiers explicites gardent l'ordre fourni."""
    logs: list[Path] = []
    explicit = False
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            found = sorted(q for q in p.iterdir() if q.suffix.lower() in EXTS)
            if not found:
                print(f"⚠ aucun {'/'.join(EXTS)} dans {p}", file=sys.stderr)
            logs += found
        elif p.is_file():
            explicit = True
            logs.append(p)
        else:
            sys.exit(f"introuvable : {p}")
    # fichiers explicites -> on respecte l'ordre donné (sauf --reverse) ; répertoire -> déjà trié.
    if reverse:
        logs = list(reversed(logs))
    elif not explicit:
        logs = sorted(logs, key=lambda q: q.name)
    return logs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Génère un rapport chirp HTML depuis des logs blackbox (.bbl/.bfl).",
        epilog="La dernière passe (après tri) est la référence (primary) du rapport.",
    )
    ap.add_argument("inputs", nargs="+",
                    help="un ou plusieurs répertoires et/ou fichiers .bbl/.bfl")
    ap.add_argument("-o", "--out", required=True, type=Path,
                    help="chemin du fichier HTML de sortie")
    ap.add_argument("--lang", choices=("fr", "en"), default="fr",
                    help="langue du rapport (défaut : fr)")
    ap.add_argument("--reverse", action="store_true",
                    help="inverse l'ordre des passes (la 1re devient la dernière/primary)")
    ap.add_argument("--json", nargs="?", const="__AUTO__", default=None, metavar="PATH",
                    help="écrit aussi un JSON d'INDICATEURS à côté (sans les courbes/heatmaps ni la "
                         "notation tune_score) ; chemin optionnel, défaut = <out>.json")
    args = ap.parse_args(argv)

    logs = collect_logs(args.inputs, args.reverse)
    if not logs:
        sys.exit("aucun log à traiter")

    passes = []
    for L in logs:
        try:
            df, fs, cfg = decode(L.read_bytes())
            passes.append(analyse_log(df, fs, cfg, file=L.name))
            print(f"  ✓ {L.name}")
        except Exception as e:  # un log corrompu ne doit pas tuer tout le lot
            print(f"  ✗ {L.name} — ignoré ({e})", file=sys.stderr)

    if not passes:
        sys.exit("aucune passe analysable")

    # Prune AVANT build_report : celui-ci slim/pop les passes en place (heatmaps, noise) — on veut les
    # indicateurs complets de chaque passe, pas la version allégée du rendu.
    json_data = None
    if args.json is not None:
        json_data = {"primary": passes[-1].get("file"), "n_passes": len(passes),
                     "passes": [_prune_indicators(p) for p in passes]}

    html = build_report(passes, lang=args.lang)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"\n{len(passes)} passe(s) → {args.out}  "
          f"(primary = {passes[-1].get('file')}, {len(html)//1024} Kio)")

    if json_data is not None:
        jpath = args.out.with_suffix(".json") if args.json == "__AUTO__" else Path(args.json)
        jpath.parent.mkdir(parents=True, exist_ok=True)
        jpath.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"   + JSON indicateurs → {jpath} ({jpath.stat().st_size // 1024} Kio)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
