"""
Methods for transforming taxonomy CSV into a graph structure backed by
networkx.
"""
# allow forward references in typing annotations
from __future__ import annotations

from typing import (ClassVar, Container, Dict, Iterable, List, Optional, Set,
                    Tuple)

import networkx as nx
import pandas as pd


class TaxonNode:
    r"""A node in a taxonomy tree, associated with a set of dataset labels.

    By default we support multiple parents for each TaxonNode because different
    taxonomies may have a different granularity of hierarchy. If the taxonomy
    was created from a mixture of different taxonomies, then we may see the
    following, for example:

        "eastern gray squirrel" (inat)     "squirrel" (gbif)
        ------------------------------     -----------------
    family:                        sciuridae
                                  /          \
    subfamily:          sciurinae             |  # skips subfamily
                                |             |
    tribe:               sciurini             |  # skips tribe
                                  \          /
    genus:                          sciurus
    """
    # class variables
    single_parent_only: ClassVar[bool] = False

    # instance variables
    level: str
    name: str
    ids: Set[Tuple[str, int]]
    graph: Optional[nx.DiGraph]
    dataset_labels: Set[Tuple[str, str]]

    def __init__(self, level: str, name: str,
                 graph: Optional[nx.DiGraph] = None):
        """Initializes a TaxonNode."""
        self.level = level
        self.name = name
        self.graph = graph
        self.ids = set()
        self.dataset_labels = set()

    def __repr__(self):
        id_str = ', '.join(f'{source}={id}' for source, id in self.ids)
        return f'TaxonNode({id_str}, level={self.level}, name={self.name})'

    @property  # read-only getter
    def parents(self) -> List[TaxonNode]:
        assert self.graph is not None
        return list(self.graph.predecessors(self))

    @parents.setter
    def parents(self, parents: Iterable[TaxonNode]) -> None:
        assert self.graph is not None
        for p in self.parents:
            self.graph.remove_edge(p, self)
        for p in parents:
            self.graph.add_edge(p, self)

    @property  # read-only getter
    def children(self) -> List[TaxonNode]:
        assert self.graph is not None
        return list(self.graph.successors(self))

    @children.setter
    def children(self, children: Iterable[TaxonNode]) -> None:
        assert self.graph is not None
        for c in self.children:
            self.graph.remove_edge(self, c)
        for c in children:
            self.graph.add_edge(self, c)

    def add_id(self, source: str, taxon_id: int) -> None:
        assert source in ['gbif', 'inat', 'manual']
        self.ids.add((source, taxon_id))

    def add_parent(self, parent: TaxonNode) -> None:
        """Adds a TaxonNode to the list of parents of the current TaxonNode.
        Requires this TaxonNode to be associated with a Graph.

        Args:
            parent: TaxonNode, must be higher in the taxonomical hierarchy
        """
        assert self.graph is not None
        parents = self.parents
        if TaxonNode.single_parent_only and len(parents) > 0:
            assert len(parents) == 1
            assert parents[0] is parent, (
                f'self.parents: {parents}, new parent: {parent}')
            return
        if parent not in parents:
            self.graph.add_edge(parent, self)

    def add_child(self, child: TaxonNode) -> None:
        """Adds a TaxonNode to the list of children of the current TaxonNode.
        Requires this TaxonNode to be associated with a Graph.

        Args:
            child: TaxonNode, must be lower in the taxonomical hierarchy
        """
        assert self.graph is not None
        self.graph.add_edge(self, child)

    def add_dataset_label(self, ds: str, ds_label: str) -> None:
        """
        Args:
            ds: str, name of dataset
            ds_label: str, name of label used by that dataset
        """
        self.dataset_labels.add((ds, ds_label))

    def get_dataset_labels(self,
                           include_datasets: Optional[Container[str]] = None
                           ) -> Set[Tuple[str, str]]:
        """Returns a set of all (ds, ds_label) tuples that belong to this taxon
        node or its descendants.

        Args:
            include_datasets: list of str, names of datasets to include
                if None, then all datasets are included

        Returns: set of (ds, ds_label) tuples
        """
        result = self.dataset_labels
        if include_datasets is not None:
            result = set(tup for tup in result if tup[0] in include_datasets)

        for child in self.children:
            result |= child.get_dataset_labels(include_datasets)
        return result

    @classmethod
    def lowest_common_ancestor(cls, nodes: Iterable[TaxonNode]
                               ) -> Optional[TaxonNode]:
        """Returns the lowest common ancestor (LCA) of a list or set of nodes.

        For each node in <nodes>, get the set of nodes on the path to the root.
        The LCA of <nodes> is certainly in the intersection of these sets.
        Iterate through the nodes in this set intersection, looking for a node
        such that none of its children is in this intersection. Given n nodes
        from a k-ary tree of height h, the algorithm runs in O((n + k)h).

        Returns: TaxonNode, the LCA if it exists, or None if no LCA exists
        """
        paths = []
        for node in nodes:
            # get path to root
            path = {node}
            remaining = list(node.parents)  # make a shallow copy
            while len(remaining) > 0:
                x = remaining.pop()
                if x not in path:
                    path.add(x)
                    remaining.extend(x.parents)
            paths.append(path)
        intersect = set.intersection(*paths)

        for node in intersect:
            if intersect.isdisjoint(node.children):
                return node
        return None


def build_taxonomy_graph(
        taxonomy_df: pd.DataFrame
        ) -> Tuple[
            nx.DiGraph,
            Dict[Tuple[str, str], TaxonNode],
            Dict[Tuple[str, str], TaxonNode]
        ]:
    """Creates a mapping from (taxon_level, taxon_name) to TaxonNodes, used for
    gathering all dataset labels associated with a given taxon.

    Args:
        taxonomy_df: pd.DataFrame, see taxonomy_mapping directory for more info

    Returns:
        graph: nx.DiGraph
        taxon_to_node: dict, maps (taxon_level, taxon_name) to a TaxonNode
        label_to_node: dict, maps (dataset_name, dataset_label) to the lowest
            TaxonNode node in the tree that contains the label
    """
    graph = nx.DiGraph()
    taxon_to_node = {}  # maps (taxon_level, taxon_name) to a TaxonNode
    label_to_node = {}  # maps (dataset_name, dataset_label) to a TaxonNode
    for _, row in taxonomy_df.iterrows():
        ds = row['dataset_name']
        ds_label = row['query']
        id_source = row['source']
        taxa_ancestry = row['taxonomy_string']
        if pd.isna(taxa_ancestry):
            # taxonomy CSV rows without 'taxonomy_string' entries can only be
            # added to the JSON via the 'dataset_labels' key
            continue
        else:
            taxa_ancestry = eval(taxa_ancestry)  # pylint: disable=eval-used

        taxon_child: Optional[TaxonNode] = None
        for i, taxon in enumerate(taxa_ancestry):
            taxon_id, taxon_level, taxon_name, _ = taxon

            key = (taxon_level, taxon_name)
            if key not in taxon_to_node:
                taxon_to_node[key] = TaxonNode(level=taxon_level,
                                               name=taxon_name, graph=graph)
            node = taxon_to_node[key]

            if taxon_child is not None:
                node.add_child(taxon_child)

            node.add_id(id_source, int(taxon_id))  # np.int64 -> int
            if i == 0:
                assert row['taxonomy_level'] == taxon_level, (
                    f'taxonomy CSV level: {row["taxonomy_level"]}, '
                    f'level from taxonomy_string: {taxon_level}')
                assert row['scientific_name'] == taxon_name
                node.add_dataset_label(ds, ds_label)
                label_to_node[(ds, ds_label)] = node

            taxon_child = node

    assert nx.is_directed_acyclic_graph(graph)
    return graph, taxon_to_node, label_to_node


def dag_to_tree(graph: nx.DiGraph,
                taxon_to_node: Dict[Tuple[str, str], TaxonNode]) -> nx.DiGraph:
    """
    NOTE: nx.is_tree(tree) might fail because tree may have disconnected
    components
    """
    tree = nx.DiGraph()
    for node in graph.nodes:
        tree.add_node(node)

        if len(node.parents) == 1:
            tree.add_edge(node.parents[0], node)

        elif len(node.parents) == 2:
            p0 = node.parents[0]
            p1 = node.parents[1]

            # use the lower parent
            if p1 in nx.descendants(graph, p0):
                tree.add_edge(p1, node)
            elif p0 in nx.descendants(graph, p1):
                tree.add_edge(p0, node)
            else:
                # special cases
                if node.name == 'cathartidae':
                    p = taxon_to_node[('order', 'accipitriformes')]
                elif node.name == 'soricidae':
                    p = taxon_to_node[('order', 'eulipotyphla')]
                elif node.name == 'nyctanassa violacea':
                    p = taxon_to_node[('genus', 'nyctanassa')]
                elif node.name == 'trochilidae':  # this one is controversial
                    p = taxon_to_node[('order', 'caprimulgiformes')]
                else:
                    assert False

                assert (p is p0) or (p is p1)
                tree.add_edge(p, node)

    for node in tree.nodes:
        node.graph = tree
    return tree
