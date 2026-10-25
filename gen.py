"""Render an OliCyber profile as a self-contained SVG card."""

from __future__ import annotations

import argparse
import base64
from collections import defaultdict
import io
import json
import os
from pathlib import Path
import sys
from uuid import UUID
from xml.sax.saxutils import escape

import requests


ROOT = Path(__file__).resolve().parent
PLAYER_URL = "https://training.olicyber.it/api/scoreboard/player"
SCOREBOARD_URL = "https://training.olicyber.it/api/scoreboard"
REQUEST_TIMEOUT = (5, 30)

# Title, labels, then chart segments; None means the theme has no chart.
TEMPLATE_COLOR_PALETTES = {
    "default": ["#6f2fb7", "#b57edc", "#8432e1", "#6f2fb7", "#a365e9", "#5a2c96", "#3d1e64"],
    "dark": ["#4a1c7c", "#6a4c93", "#2c1047", "#4a1c7c", "#6a4c93", "#281847", "#1a1a2e"],
    "white": ["#6a5acd", "#8a7db0", "#d8d8f0", "#6a5acd", "#8a7db0", "#e6e6fa", "#f5f5ff"],
    "darkRed": None,
    "hacker": None,
    "oldDefault": None,
}


def validate_token(token):
    """Validate credentials at runtime, never as an import side effect."""
    if not token or not token.strip():
        raise ValueError("Set OC_TOKEN to your OliCyber training-platform token, or use --sample.")
    token = token.strip()
    try:
        parsed = UUID(token)
    except (ValueError, AttributeError):
        raise ValueError("OC_TOKEN must be a UUID in the form xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx.") from None
    if str(parsed) != token.lower():
        raise ValueError("OC_TOKEN must use the hyphenated UUID format.")
    return token


def fetch_json(session, url, headers, **kwargs):
    try:
        response = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT, **kwargs)
        response.raise_for_status()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        if status in (401, 403):
            raise ValueError("OliCyber rejected OC_TOKEN. Sign in again and update your token.") from None
        raise ValueError(f"OliCyber returned HTTP {status}; try again later.") from None
    except requests.RequestException:
        raise ValueError("Could not reach OliCyber (network error or request timeout); try again later.") from None
    try:
        return response.json()
    except ValueError:
        raise ValueError("OliCyber returned invalid JSON; try again later.") from None


def fetch_profile(token):
    headers = {"Authorization": f"Token {validate_token(token)}"}
    with requests.Session() as session:
        player = fetch_json(session, PLAYER_URL, headers)
        scoreboard = fetch_json(session, SCOREBOARD_URL, headers, params={"noFreeze": False})
    return player, scoreboard["scoreboard"]


def resolve_fullname(player_data):
    return f'{player_data["name"]} {player_data["surname"]} ({player_data["nickname"]})'


def find_scoreboard_position(player_data, scoreboard):
    display_name = resolve_fullname(player_data)
    return next(
        (position for position, player in enumerate(scoreboard, start=1)
         if player.get("displayedName") == display_name),
        "N/A",
    )


def find_best_category(player_data):
    categories = player_data["categories"]
    if not categories or not any(value["solves"] for value in categories.values()):
        return "N/A"
    return max(categories, key=lambda category: categories[category]["solves"]).capitalize()


def summarize_player(player, scoreboard):
    return {
        "scoreboard_position": find_scoreboard_position(player, scoreboard),
        "username": player["nickname"],
        "solved_challenges": player["correctSubmissions"],
        "score": player["score"],
        "best_category": find_best_category(player),
    }


def generate_histogram_svg(categories, template="default"):
    """Return a base64 SVG pie chart, or an empty string for zero solves."""
    palette = TEMPLATE_COLOR_PALETTES[template]
    nonzero = {name: solves for name, solves in categories.items() if solves > 0}
    if palette is None or not nonzero:
        return ""

    # A noninteractive canvas also works in Actions and on headless machines.
    from matplotlib import rc_context
    from matplotlib.figure import Figure

    title_color, text_color, *chart_colors = palette
    figure = Figure(figsize=(6, 3), dpi=100)
    axes = figure.subplots()
    axes.set_title("Completed Challenges", fontsize=14, color=title_color, fontweight="bold")
    axes.pie(
        list(nonzero.values()), labels=list(nonzero), colors=chart_colors,
        labeldistance=1.1,
        wedgeprops={"edgecolor": "black", "linewidth": 1, "antialiased": True},
        textprops={"color": text_color, "fontsize": 10, "fontweight": "bold"},
    )
    buffer = io.BytesIO()
    # Stable IDs and no timestamp keep the daily workflow from committing noise.
    with rc_context({"svg.hashsalt": "ocbadge-themes"}):
        figure.savefig(buffer, format="svg", bbox_inches="tight", transparent=True,
                       metadata={"Date": None})
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def make_card(data, categories, template="default"):
    if template not in TEMPLATE_COLOR_PALETTES:
        raise ValueError(f"Unknown theme: {template}. Choose from {', '.join(TEMPLATE_COLOR_PALETTES)}.")

    # Values from the network are text, never SVG markup or attributes.
    values = {key: escape(str(value), {'"': "&quot;", "'": "&apos;"}) for key, value in data.items()}
    values["logo"] = base64.b64encode((ROOT / "data" / "logo.svg").read_bytes()).decode("ascii")
    chart = generate_histogram_svg(categories, template)
    values["histogram"] = f"data:image/svg+xml;base64,{chart}" if chart else ""
    source = (ROOT / "data" / f"template_{template}.svg").read_text(encoding="utf-8")
    return source.format_map(defaultdict(lambda: "N/A", values))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("style", nargs="?", choices=TEMPLATE_COLOR_PALETTES, default="default")
    parser.add_argument("--output", type=Path, default=Path("card.svg"), help="SVG destination (default: card.svg)")
    parser.add_argument("--data-output", type=Path, help="optionally export the displayed public stats as JSON")
    parser.add_argument("--sample", action="store_true", help="render fictional sample data offline, without OC_TOKEN")
    args = parser.parse_args(argv)

    try:
        if args.sample:
            sample = json.loads((ROOT / "examples" / "sample-player.json").read_text(encoding="utf-8"))
            player, scoreboard = sample["player"], sample["scoreboard"]
        else:
            player, scoreboard = fetch_profile(os.getenv("OC_TOKEN"))
        data = summarize_player(player, scoreboard)
        categories = {name.capitalize(): value["solves"] for name, value in player["categories"].items()}
        card = make_card(data, categories, args.style)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(card, encoding="utf-8")
        if args.data_output:
            args.data_output.parent.mkdir(parents=True, exist_ok=True)
            args.data_output.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Saved {args.style} card to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
