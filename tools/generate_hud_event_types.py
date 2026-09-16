"""Genera l'elenco C++ di EventType (F4.1.2, "generare o condividere lo schema tra Python e C++;
niente enum mantenuti a mano"): prima di questo strumento, hud/native/src/JakeClient.cpp
confrontava direttamente stringhe letterali ("USER_MESSAGE", "HUD_SHOW"...) scritte a mano,
senza alcuna garanzia che restassero sincronizzate con core/hud_protocol.py::EventType - un nuovo
tipo aggiunto lato Python (o un nome rinominato) poteva silenziosamente disallinearsi dal lato
C++, scoperto solo a runtime (o mai, se il tipo dimenticato capitava raramente).

Questo strumento legge l'ENUM VERO (non una copia mantenuta a mano di cosa contiene) e genera un
header C++ con le stesse costanti, cosi' un tipo nuovo/rinominato in Python fa fallire la build
C++ (o comunque produce un header aggiornato automaticamente) invece di disallinearsi in
silenzio. Eseguito da CMake come build step (vedi hud/native/CMakeLists.txt), non a mano - il
file generato non va mai modificato direttamente ne' committato (vedi .gitignore).

Uso:
    python -m tools.generate_hud_event_types --output percorso/HudEventTypes.h"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.hud_protocol import (  # noqa: E402
    EventType, HUD_PAYLOAD_RULES, HUD_SEQUENCE_ID_MAX, HUD_STEP_MAX, HUD_VERIFICATION_VALUES,
)

_HEADER_TEMPLATE = """// GENERATO AUTOMATICAMENTE da tools/generate_hud_event_types.py - NON MODIFICARE A MANO.
// Fonte: core/hud_protocol.py::EventType. Rigenerato ad ogni build (vedi CMakeLists.txt) cosi'
// un tipo aggiunto o rinominato lato Python si riflette qui senza bisogno di tenerlo a mano in
// sincronia (F4.1.2, "niente enum mantenuti a mano").
#pragma once
#include <array>
#include <cstdint>

namespace JakeHudEventType {{
{constants}
inline constexpr std::array<const char *, {event_count}> ALL{{{{{event_names}}}}};
}}

namespace JakeHudContract {{
struct PayloadRule {{ const char *event; const char *key; const char *kind; }};
inline constexpr std::array<PayloadRule, {rule_count}> PAYLOAD_RULES{{{{
{rules}
}}}};
inline constexpr std::array<const char *, {verification_count}> VERIFICATION_VALUES{{{{{verification_values}}}}};
inline constexpr std::int64_t SEQUENCE_ID_MAX = {sequence_max};
inline constexpr std::int64_t STEP_MAX = {step_max};
}}
"""


def generate_header(event_type_names: list) -> str:
    constants = "\n".join(
        f'inline constexpr const char *{name} = "{name}";' for name in event_type_names
    )
    rules = [f'    {{"{event}", "{key}", "{kind}"}},' for event, fields in HUD_PAYLOAD_RULES.items()
             if event == "*" or event in event_type_names for key, kind in fields.items()]
    return _HEADER_TEMPLATE.format(
        constants=constants, event_count=len(event_type_names), event_names=", ".join(event_type_names),
        rule_count=len(rules), rules="\n".join(rules),
        verification_count=len(HUD_VERIFICATION_VALUES),
        verification_values=", ".join(f'"{value}"' for value in HUD_VERIFICATION_VALUES),
        sequence_max=HUD_SEQUENCE_ID_MAX, step_max=HUD_STEP_MAX,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="percorso del file .h da scrivere")
    args = parser.parse_args()

    event_type_names = [member.name for member in EventType]
    header = generate_header(event_type_names)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(header, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
