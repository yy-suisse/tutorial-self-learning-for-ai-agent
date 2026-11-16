from dataclasses import dataclass


@dataclass
class BasicConfig:
    graph_path: str = "D:/HUG_graph_data/2026-07-03/"

    relation_path: str = f"{graph_path}connectivity.parquet"
    concept_path: str = f"{graph_path}concept_snomed_hug.parquet"
    mapped_path: str = f"{graph_path}mapped_concepts.parquet"
    official_release_path: str = f"{graph_path}released_version.parquet"
