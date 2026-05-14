"""
Storage layer health check script.

Verify data integrity across PostgreSQL, Neo4j, and Qdrant after offline migration.

Run:
    uv run python src/check_storage.py
"""

import asyncio
import importlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def _load_env_file(env_path: Path) -> None:
    """Load KEY=VALUE pairs from .env without overriding existing vars."""
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _require_env(var_name: str) -> str:
    value = os.getenv(var_name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {var_name}")
    return value


async def check_postgresql() -> dict[str, Any]:
    """Check PostgreSQL storage and return statistics."""
    logger = logging.getLogger(__name__)
    logger.info("=" * 70)
    logger.info("CHECKING PostgreSQL")
    logger.info("=" * 70)

    try:
        pg_host = _require_env("POSTGRES_HOST")
        pg_port = int(_require_env("POSTGRES_PORT"))
        pg_user = _require_env("POSTGRES_USER")
        pg_pwd = _require_env("POSTGRES_PASSWORD")
        pg_db = _require_env("POSTGRES_DATABASE")

        asyncpg = importlib.import_module("asyncpg")
        conn = await asyncpg.connect(
            host=pg_host, port=pg_port, user=pg_user, password=pg_pwd, database=pg_db
        )

        workspace = os.getenv("PG_WORKSPACE", "viet_contract_prod")
        logger.info(f"Workspace: {workspace}")

        # Get all table names from the database
        all_tables = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name"
        )
        all_table_names = [t["table_name"] for t in all_tables]

        logger.info(f"\n  Found {len(all_table_names)} tables:")
        results = {}
        for table_name in all_table_names:
            try:
                count = await conn.fetchval(f"SELECT COUNT(*) FROM {table_name}")
                logger.info(f"    {table_name:<50} {count:>8} records")
                results[table_name] = count
            except Exception as e:
                logger.warning(f"    {table_name:<50} ERROR: {e}")
                results[table_name] = None

        await conn.close()

        total_records = sum(v for v in results.values() if v is not None)
        pg_status = {
            "host": pg_host,
            "port": pg_port,
            "database": pg_db,
            "workspace": workspace,
            "table_count": len(results),
            "tables": results,
            "total_records": total_records,
            "status": "healthy" if total_records > 0 else "empty",
        }

        logger.info(f"\nPostgreSQL status: {pg_status['status']}")
        logger.info(f"Total records (all tables): {pg_status['total_records']}")
        return pg_status

    except Exception as e:
        logger.error(f"PostgreSQL check failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


async def check_neo4j() -> dict[str, Any]:
    """Check Neo4j graph storage and return statistics."""
    logger = logging.getLogger(__name__)
    logger.info("\n" + "=" * 70)
    logger.info("CHECKING Neo4j")
    logger.info("=" * 70)

    try:
        neo4j = importlib.import_module("neo4j")
        uri = _require_env("NEO4J_URI")
        user = _require_env("NEO4J_USERNAME")
        pwd = _require_env("NEO4J_PASSWORD")

        driver = neo4j.AsyncGraphDatabase.driver(uri, auth=(user, pwd))

        workspace = os.getenv("NEO4J_WORKSPACE", "viet_contract_prod")
        logger.info(f"URI: {uri}, Workspace: {workspace}")

        async with driver.session() as session:
            # Count nodes
            result = await session.run("MATCH (n) RETURN COUNT(n) AS count")
            records = await result.fetch(1)
            node_total = records[0]["count"] if records else 0
            logger.info(f"  Total nodes: {node_total}")

            # Count edges
            result = await session.run("MATCH ()-[r]->() RETURN COUNT(r) AS count")
            records = await result.fetch(1)
            edge_total = records[0]["count"] if records else 0
            logger.info(f"  Total edges: {edge_total}")

            # Node type distribution
            logger.info("\n  Node types distribution:")
            result = await session.run(
                "MATCH (n) RETURN labels(n) AS labels, COUNT(*) AS count ORDER BY count DESC LIMIT 10"
            )
            node_types = await result.fetch(10)
            for record in node_types:
                labels = record["labels"]
                count = record["count"]
                label_str = "|".join(labels) if labels else "(no label)"
                logger.info(f"    {label_str:<50} {count:>8} nodes")

            # Sample node
            try:
                result = await session.run("MATCH (n) RETURN n LIMIT 1")
                records = await result.fetch(1)
                if records:
                    node_data = records[0]["n"]
                    logger.info(f"\n  Sample node: {dict(node_data)}")
            except Exception as e:
                logger.warning(f"Could not fetch sample node: {e}")

        await driver.close()

        neo4j_status = {
            "uri": uri,
            "workspace": workspace,
            "nodes": node_total,
            "edges": edge_total,
            "status": "healthy" if node_total > 0 and edge_total > 0 else "incomplete",
        }

        logger.info(f"\nNeo4j status: {neo4j_status['status']}")
        return neo4j_status

    except Exception as e:
        logger.error(f"Neo4j check failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


async def check_qdrant() -> dict[str, Any]:
    """Check Qdrant vector storage and return statistics."""
    logger = logging.getLogger(__name__)
    logger.info("\n" + "=" * 70)
    logger.info("CHECKING Qdrant")
    logger.info("=" * 70)

    try:
        qdrant_client = importlib.import_module("qdrant_client")

        url = _require_env("QDRANT_URL")
        api_key = os.getenv("QDRANT_API_KEY", "")

        # Use sync client for simplicity
        client = qdrant_client.QdrantClient(url=url, api_key=api_key if api_key else None, timeout=10.0)

        workspace = os.getenv("QDRANT_WORKSPACE", "viet_contract_prod")
        logger.info(f"URL: {url}, Workspace: {workspace}")

        # List collections
        collections = client.get_collections()
        logger.info(f"\n  Collections ({len(collections.collections)}):")

        results = {}
        for collection_info in collections.collections:
            collection_name = collection_info.name
            try:
                # Get collection stats
                stats = client.get_collection(collection_name)
                points_count = stats.points_count

                logger.info(f"    {collection_name:<60} points={points_count:>8}")

                results[collection_name] = {
                    "points": points_count,
                }
            except Exception as e:
                logger.warning(f"    {collection_name:<60} ERROR: {e}")
                results[collection_name] = {"error": str(e)}

        client.close()

        total_points = sum(
            v.get("points", 0) for v in results.values() 
            if isinstance(v, dict) and "points" in v
        )

        qdrant_status = {
            "url": url,
            "workspace": workspace,
            "collections": results,
            "total_collections": len(results),
            "total_points": total_points,
            "status": "healthy" if total_points > 0 else "empty",
        }

        logger.info(f"\nQdrant status: {qdrant_status['status']}")
        logger.info(f"Total points (all collections): {qdrant_status['total_points']}")
        return qdrant_status

    except Exception as e:
        logger.error(f"Qdrant check failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


async def main() -> None:
    """Run all storage health checks."""
    _load_env_file(ROOT / ".env")

    print("\n" + "=" * 70)
    print("VIET CONTRACT AUDITOR – Storage Layer Health Check")
    print("=" * 70 + "\n")

    # Run checks sequentially
    pg_status = await check_postgresql()
    neo4j_status = await check_neo4j()
    qdrant_status = await check_qdrant()

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"\nPostgreSQL: {pg_status.get('status', 'unknown')}")
    print(f"  Total records: {pg_status.get('total_records', 0)}")

    print(f"\nNeo4j: {neo4j_status.get('status', 'unknown')}")
    print(f"  Nodes: {neo4j_status.get('nodes', 0)}, Edges: {neo4j_status.get('edges', 0)}")

    print(f"\nQdrant: {qdrant_status.get('status', 'unknown')}")
    print(f"  Collections: {qdrant_status.get('total_collections', 0)}, Total points: {qdrant_status.get('total_points', 0)}")

    # Overall status
    all_healthy = all(
        status.get("status") == "healthy"
        for status in [pg_status, neo4j_status, qdrant_status]
        if "status" in status
    )

    print("\n" + "=" * 70)
    if all_healthy:
        print("[OK] All storage layers are healthy!")
    else:
        print("[WARN] Some storage layers have issues. Review logs above.")
    print("=" * 70 + "\n")

    # Export summary to JSON
    summary = {
        "timestamp": __import__("datetime").datetime.now().isoformat(),
        "postgresql": pg_status,
        "neo4j": neo4j_status,
        "qdrant": qdrant_status,
        "overall_status": "healthy" if all_healthy else "issues_detected",
    }

    summary_path = ROOT / "storage_health_check.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"Health check summary saved to: {summary_path}\n")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt="%H:%M:%S")
    asyncio.run(main())
