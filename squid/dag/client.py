"""Async Neo4j driver wrapper for ResearchSquid DAG."""

import os
from contextlib import asynccontextmanager
from typing import Optional

from neo4j import AsyncGraphDatabase, AsyncDriver


class DAGClient:
    """Async Neo4j driver wrapper."""

    def __init__(
        self,
        uri: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self.uri = uri or os.getenv("NEO4J_URI", "bolt://neo4j:7687")
        self.user = user or os.getenv("NEO4J_USER", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD", "researchsquid")
        self._driver: Optional[AsyncDriver] = None

    async def connect(self):
        self._driver = AsyncGraphDatabase.driver(
            self.uri, auth=(self.user, self.password)
        )

    async def close(self):
        if self._driver:
            await self._driver.close()

    @property
    def driver(self) -> AsyncDriver:
        if not self._driver:
            raise RuntimeError("Not connected — call await client.connect() first")
        return self._driver

    async def run(self, query: str, **params):
        async with self.driver.session() as session:
            result = await session.run(query, params)
            return [record async for record in result]

    async def run_write(self, query: str, **params):
        async with self.driver.session() as session:
            result = await session.execute_write(
                lambda tx: tx.run(query, params)
            )
            return result
