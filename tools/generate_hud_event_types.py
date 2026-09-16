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

from core.hud_protocol import EventType  # noqa: E402

_HEADER_TEMPLATE = """// GENERATO AUTOMATICAMENTE da tools/generate_hud_event_types.py - NON MODIFICARE A MANO.
// Fonte: core/hud_protocol.py::EventType. Rigenerato ad ogni build (vedi CMakeLists.txt) cosi'
// un tipo aggiunto o rinominato lato Python si riflette qui senza bisogno di tenerlo a mano in
// sincronia (F4.1.2, "niente enum mantenuti a mano").
#pragma once

namespace JakeHudEventType {{
{constants}
}}
"""


def generate_header(event_type_names: list) -> str:
    constants = "\n".join(
        f'inline constexpr const char *{name} = "{name}";' for name in event_type_names
    )
    return _HEADER_TEMPLATE.format(constants=constants)


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
