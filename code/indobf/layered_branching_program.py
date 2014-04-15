from __future__ import print_function

import networkx as nx

from sage.all import copy, GF, MatrixSpace, VectorSpace, ZZ

from branchingprogram import AbstractBranchingProgram, ParseException, Layer
import utils

class _Graph(object):
    def __init__(self, inp, graph, nlayers, num):
        self.inp = inp
        self.graph = graph
        self.nlayers = nlayers
        self.num = num

def relabel(g, num):
    new = [(s, num) for (s, _) in g.nodes()]
    return nx.relabel_nodes(g, dict(zip(g.nodes(), new)))

def contract(g, a, b, name):
    new = 'tmp'
    g.add_node(new)
    for node in g.predecessors(a):
        g.add_edge(node, new, label=g.edge[node][a]['label'])
    for node in g.neighbors(b):
        g.add_edge(new, node, label=g.edge[b][node]['label'])
    for node in g.predecessors(b):
        g.add_edge(node, new, label=g.edge[node][b]['label'])
    g.remove_node(a)
    g.remove_node(b)
    g = nx.relabel_nodes(g, {new: name})
    return g

class LayeredBranchingProgram(AbstractBranchingProgram):
    def __init__(self, fname, verbose=False):
        super(LayeredBranchingProgram, self).__init__(verbose=verbose)
        self.graph = None
        self.length = None
        self.nlayers = 0
        self._load_formula(fname)

    def _load_formula(self, fname):
        bp = []
        self.nlayers = 0
        def _new_gate(num):
            g = nx.digraph.DiGraph()
            g.add_node(('src', num), layer=1)
            g.add_node(('acc', num))
            g.add_node(('rej', num))
            g.add_edge(('src', num), ('acc', num), label=1)
            g.add_edge(('src', num), ('rej', num), label=0)
            def eval(inp):
                if inp == 1:
                    return num
                else:
                    raise Exception("eval failed on %s!" % inp)
            return _Graph(eval, g, 1, num)
        def _and_gate(num, idx1, idx2):
            bp1 = bp[idx1]
            bp2 = bp[idx2]
            t1 = bp1.nlayers
            t2 = bp2.nlayers
            g = nx.union(bp1.graph, bp2.graph)
            g = contract(g, ('acc', idx1), ('src', idx2), ('node-%d' % num, num))
            g = contract(g, ('rej', idx1), ('rej', idx2), ('rej', num))
            g = relabel(g, num)
            g.node[('node-%d' % num, num)]['layer'] = t1 + t2
            def eval(inp):
                if inp <= t1:
                    return bp1.inp(inp)
                elif inp <= t1 + t2:
                    return bp2.inp(t1 + t2 - inp + 1)
                else:
                    raise Exception("eval failed on %s!" % inp)
            return _Graph(eval, g, t1 + t2, num)
        def _id_gate(num, idx):
            return bp[idx]
        def _not_gate(num, idx):
            bp1 = bp[idx]
            g = nx.relabel_nodes(bp1.graph, {('acc', idx): ('rej', idx),
                                             ('rej', idx): ('acc', idx)})
            g = relabel(g, num)
            return _Graph(bp1.inp, g, bp1.nlayers, num)
        gates = {
            'ID': _id_gate,
            'AND': _and_gate,
            'NOT': _not_gate,
        }
        output = False
        with open(fname) as f:
            for line in f:
                if line.startswith('#') or line.startswith(':'):
                    continue
                num, rest = line.split(None, 1)
                try:
                    num = int(num)
                except ValueError:
                    raise ParseException("gate index not a number")
                if rest.startswith('input'):
                    bp.append(_new_gate(num))
                    self.nlayers += 1
                elif rest.startswith('gate') or rest.startswith('output'):
                    if rest.startswith('output'):
                        if output:
                            raise ParseException('only support single output gate')
                        else:
                            output = True
                    _, gate, rest = rest.split(None, 2)
                    inputs = [int(i) for i in rest.split()]
                    # try:
                    bp.append(gates[gate.upper()](num, *inputs))
                    # except KeyError:
                    #     raise ParseException("unsupported gate '%s'" % gate)
                    # except TypeError:
                    #     raise ParseException("incorrect number of arguments given")
                else:
                    raise ParseException("unknown type")
        if not output:
            raise ParseException("no output gate found")
        self.graph = bp[-1]
        self.length = len(self.graph.graph)
        self._to_relaxed_matrix_bp()

    def _to_relaxed_matrix_bp(self):
        g = self.graph.graph
        n = self.nlayers
        G = MatrixSpace(GF(2), self.length)
        nodes = nx.topological_sort(g)
        if nodes.index(('acc', self.graph.num)) != len(nodes) - 1:
            a = nodes.index(('acc', self.graph.num))
            b = nodes.index(('rej', self.graph.num))
            nodes[b], nodes[a] = nodes[a], nodes[b]
        mapping = dict(zip(nodes, range(self.length)))
        g = nx.relabel_nodes(g, mapping)
        self.bp = []
        for layer in xrange(1, self.nlayers + 1):
            B0 = copy(G.one())
            B1 = copy(G.one())
            for edge in g.edges_iter():
                e = g[edge[0]][edge[1]]
                assert e['label'] in (0, 1)
                if g.node[edge[0]]['layer'] == layer:
                    if e['label'] == 0:
                        B0[edge[0], edge[1]] = 1
                    else:
                        B1[edge[0], edge[1]] = 1
            self.bp.append(Layer(self.graph.inp(layer), B0, B1))

    def randomize(self, prime):
        assert not self.randomized
        MSZp = MatrixSpace(ZZ.residue_field(ZZ.ideal(prime)), self.length)
        def random_matrix():
            while True:
                m = MSZp.random_element()
                if not m.is_singular() and m.rank() == self.length:
                    return m, m.inverse()
        m0, m0i = random_matrix()
        self.bp[0] = self.bp[0].group(MSZp).mult_left(m0)
        for i in xrange(1, len(self.bp)):
            mi, mii = random_matrix()
            self.bp[i-1] = self.bp[i-1].group(MSZp).mult_right(mii)
            self.bp[i] = self.bp[i].group(MSZp).mult_left(mi)
        self.bp[-1] = self.bp[-1].group(MSZp).mult_right(m0i)
        VSZp = VectorSpace(ZZ.residue_field(ZZ.ideal(prime)), self.length)
        self.e_1 = copy(VSZp.zero())
        self.e_1[0] = 1
        self.e_w = copy(VSZp.zero())
        self.e_w[len(self.e_w) - 1] = 1
        self.m0, self.m0i = m0, m0i
        self.randomized = True

    def _eval_layered_bp(self, inp):
        assert self.graph
        g = self.graph.graph.copy()
        nodes = nx.get_node_attributes(g, 'layer')
        for layer in xrange(1, self.nlayers + 1):
            choice = 0 if inp[self.graph.inp(layer)] == '0' else 1
            for node in nodes:
                if g.node[node]['layer'] == layer:
                    for neighbor in g.neighbors(node):
                        if g.edge[node][neighbor]['label'] != choice:
                            g.remove_edge(node, neighbor)
        try:
            nx.dijkstra_path(g, ('src', self.graph.num), ('acc', self.graph.num))
            return 1
        except nx.NetworkXNoPath:
            return 0
                    
    def _eval_relaxed_matrix_bp(self, inp):
        assert self.bp
        m = self.bp[0]
        comp = m.zero if inp[m.inp] == '0' else m.one
        for i, m in enumerate(self.bp[1:]):
            comp *= m.zero if inp[m.inp] == '0' else m.one
        if self.randomized:
            r = self.e_1 * self.m0i * comp * self.m0 * self.e_w
            return 1 if r == 1 else 0
        else:
            return 1 if comp[0, comp.nrows() - 1] == 1 else 0

    def evaluate(self, inp):
        assert self.bp or self.graph
        if self.bp is None:
            return self._eval_layered_bp(inp)
        else:
            return self._eval_relaxed_matrix_bp(inp)
