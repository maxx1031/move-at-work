"""MCP server exposing the ExRx exercise graph as read-only tools (stdio by default).

    python exrx_db/mcp_server.py                         # stdio, for Claude Code / Desktop
    python exrx_db/mcp_server.py --http 127.0.0.1:8765   # streamable HTTP, for other agents
"""
import argparse
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from exrx_db.query import ExRx  # noqa: E402

server = MCPServer(
    name="exrx",
    instructions=(
        "Personal ExRx exercise library. Start with search_exercises (free text, e.g. a description "
        "of the movement in an image), then get_exercise for details. Use list_values to discover "
        "valid filter values."
    ),
)
_db = None


def db():
    global _db
    if _db is None:
        _db = ExRx()
    return _db


@server.tool()
def search_exercises(query: str, section: str | None = None, equipment: str | None = None,
                     muscle: str | None = None, role: str | None = None, limit: int = 10) -> list:
    """Hybrid full-text + semantic exercise search with optional section/equipment/muscle/role filters."""
    return db().search_exercises(query, section, equipment, muscle, role, limit)


@server.tool()
def get_exercise(exercise_id: str) -> dict:
    """Full exercise card: classification, instructions, muscles by role, joint actions, media, variations."""
    return db().get_exercise(exercise_id)


@server.tool()
def exercises_for_muscle(muscle: str, role: str | None = None, limit: int = 50) -> list:
    """Exercises that work a muscle (name substring), optionally only as target/synergists/stabilizers."""
    return db().exercises_for_muscle(muscle, role, limit)


@server.tool()
def exercises_for_joint_action(joint: str, action: str | None = None, kind: str | None = None,
                               limit: int = 50) -> list:
    """Exercises by joint action, e.g. joint='Hip', action='Extension', kind='dynamic'."""
    return db().exercises_for_joint_action(joint, action, kind, limit)


@server.tool()
def similar_exercises(exercise_id: str, limit: int = 10) -> list:
    """Exercises most similar to the given one."""
    return db().similar_exercises(exercise_id, limit)


@server.tool()
def list_values(field: str) -> list:
    """Valid filter values. field: sections | equipment | muscles | joints | roles | actions."""
    return db().list_values(field)


@server.tool()
def run_surrealql(query: str):
    """Read-only SurrealQL against tables exercise, muscle, joint, equipment and edges works/moves/uses/variation_of."""
    return db().run_surrealql(query)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", metavar="HOST:PORT", help="serve streamable HTTP instead of stdio")
    args = ap.parse_args()
    if args.http:
        host, port = args.http.rsplit(":", 1)
        server.run("streamable-http", host=host, port=int(port))
    else:
        server.run()
