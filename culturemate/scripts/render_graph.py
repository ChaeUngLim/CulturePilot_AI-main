"""그래프 구조를 Mermaid로 덤프한다. 문서의 다이어그램은 여기서 생성한 것이다.

    python scripts/render_graph.py            # 메인 그래프
    python scripts/render_graph.py archive    # 서브그래프
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.graph.build import build_graph
from app.graph.subgraphs.archive import build_archive_graph
from app.graph.subgraphs.discovery import build_discovery_graph
from app.graph.subgraphs.itinerary import build_itinerary_graph
from app.graph.subgraphs.validation import build_validation_graph

BUILDERS = {
    "main": build_graph,
    "archive": build_archive_graph,
    "discovery": build_discovery_graph,
    "itinerary": build_itinerary_graph,
    "validation": build_validation_graph,
}

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "main"
    print(BUILDERS[name]().get_graph(xray=1).draw_mermaid())
