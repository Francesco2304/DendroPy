#! /usr/bin/env python

##############################################################################
##  DendroPy Phylogenetic Computing Library.
##
##  Copyright 2010-2014 Jeet Sukumaran and Mark T. Holder.
##  All rights reserved.
##
##  See "LICENSE.txt" for terms and conditions of usage.
##
##  If you use this work or any portion thereof in published work,
##  please cite it as:
##
##     Sukumaran, J. and M. T. Holder. 2010. DendroPy: a Python library
##     for phylogenetic computing. Bioinformatics 26: 1569-1571.
##
##############################################################################

"""
Testing of calculation of and operations with split bitmask hashes.
"""

import warnings
import unittest
import re
import sys
try:
    from StringIO import StringIO # Python 2 legacy support: StringIO in this module is the one needed (not io)
except ImportError:
    from io import StringIO # Python 3

from dendropy.test.support import pathmap
from dendropy.test.support import paupsplitsreference
from dendropy.test.support.dendropytest import ExtendedTestCase
from dendropy.utility import messaging
from dendropy.interop import paup
from dendropy.calculate import treesplit
from dendropy.calculate import treecompare
import dendropy

_LOG = messaging.get_logger(__name__)

class SplitDistributionTestCases(ExtendedTestCase):

    def check_splits_distribution(self,
            tree_filename,
            splits_filename,
            ignore_tree_weights,
            is_rooted,
            expected_num_trees,
            ):

        if is_rooted:
            key_column_index = 2 # unnormalized
        else:
            key_column_index = 1 # normalized
        splits_ref = paupsplitsreference.get_splits_reference(
                splits_filename=splits_filename,
                key_column_index=key_column_index,
                )

        print("* {} ({})".format(tree_filename, splits_filename))
        tree_filepath = pathmap.tree_source_path(tree_filename)
        trees = dendropy.TreeList.get_from_path(
                tree_filepath,
                "nexus",
                store_tree_weights=not ignore_tree_weights)
        sd = treesplit.SplitDistribution(
                taxon_namespace=trees.taxon_namespace,
                ignore_tree_weights=ignore_tree_weights)
        for tree in trees:
            sd.count_splits_on_tree(tree)

        # trees counted ...
        self.assertEqual(sd.total_trees_counted, len(trees))
        # frequencies have not yet been calculated
        self.assertEqual(sd._trees_counted_for_freqs, 0)
        self.assertFalse(sd.is_mixed_rootings_counted())
        if is_rooted:
            self.assertTrue(sd.is_all_counted_trees_rooted())
        else:
            self.assertFalse(sd.is_all_counted_trees_rooted())
            self.assertTrue(sd.is_all_counted_trees_treated_as_unrooted() or sd.is_all_counted_trees_strictly_unrooted())

        # splits_distribution also counts trivial splits, so this will not work
        # self.assertEqual(len(splits_ref), len(sd))

        expected_nontrivial_splits = set(splits_ref.keys())
        observed_splits = set(sd.split_counts.keys())
        # for k in sorted(observed_splits):
        #     print("{}: {}, {}".format(k, sd.split_counts[k], sd[k]))
        for split in expected_nontrivial_splits:
            # print("{}: {} vs {}".format(split, sd[split], splits_ref[split]["count"]))
            self.assertAlmostEqual(sd.split_counts[split], splits_ref[split]["count"], 2)
            # self.assertIn(split, observed_splits, sorted(observed_splits))

        # for split in expected_nontrivial_splits:
        #     self.assert


        # self.assertEqual(len(splits_ref), len(bipartition_freqs))
        # if is_rooted:
        #     splits_ref_bitmasks = set([splits_ref[x]["unnormalized_split_bitmask"] for x in splits_ref])
        # else:
        #     splits_ref_bitmasks = set([splits_ref[x]["normalized_split_bitmask"] for x in splits_ref])
        # counts_keys = set(bipartition_counts.keys())
        # freqs_keys = set(bipartition_freqs.keys())
        # self.assertEqual(len(counts_keys), len(splits_ref_bitmasks))
        # self.assertEqual(counts_keys, splits_ref_bitmasks, "\n    {}\n\n    {}\n\n".format(sorted(counts_keys), sorted(splits_ref_bitmasks)))
        # for split_str_rep in splits_ref:
        #     ref = splits_ref[split_str_rep]
        #     self.assertEqual(split_str_rep, ref["bipartition_string"])
        #     self.assertEqual(paup.PaupService.bipartition_groups_to_split_bitmask(split_str_rep, normalized=False),
        #             ref["unnormalized_split_bitmask"])
        #     self.assertEqual(paup.PaupService.bipartition_groups_to_split_bitmask(split_str_rep, normalized=True),
        #             ref["normalized_split_bitmask"])
        #     split_bitmask = paup.PaupService.bipartition_groups_to_split_bitmask(split_str_rep, normalized=not is_rooted)
        #     self.assertEqual(bipartition_counts[split_bitmask], ref["count"])
        #     self.assertAlmostEqual(bipartition_freqs[split_bitmask], ref["frequency"])

    def test_group1(self):
        sources = [
                # ("cetaceans.mb.no-clock.mcmc.trees"    , 251, False, False), # Trees explicitly unrooted
                ("cetaceans.mb.no-clock.mcmc.weighted-01.trees" , 251, False , True), # Weighted
                ("cetaceans.mb.no-clock.mcmc.weighted-02.trees" , 251, False , True), # Weighted
                ("cetaceans.mb.no-clock.mcmc.weighted-03.trees" , 251, False , True), # Weighted
                ("cetaceans.mb.strict-clock.mcmc.trees", 251, True , False), # Trees explicitly rooted
                ("cetaceans.mb.strict-clock.mcmc.weighted-01.trees" , 251, True , True), # Weighted
                ("cetaceans.mb.strict-clock.mcmc.weighted-02.trees" , 251, True , True), # Weighted
                ("cetaceans.mb.strict-clock.mcmc.weighted-03.trees" , 251, True , True), # Weighted
                ("cetaceans.raxml.bootstraps.trees"    , 250, None , False), # No tree rooting statement; PAUP defaults to rooted, DendroPy defaults to unrooted
                ("cetaceans.raxml.bootstraps.weighted-01.trees"    , 250, None , False), # No tree rooting statement; PAUP defaults to rooted, DendroPy defaults to unrooted
                ("cetaceans.raxml.bootstraps.weighted-02.trees"    , 250, None , False), # No tree rooting statement; PAUP defaults to rooted, DendroPy defaults to unrooted
                ("cetaceans.raxml.bootstraps.weighted-03.trees"    , 250, None , False), # No tree rooting statement; PAUP defaults to rooted, DendroPy defaults to unrooted
                ("issue_mth_2009-02-03.rooted.nexus"   , 100, True , False), # 100 trees (frequency column not reported by PAUP)
                ("issue_mth_2009-02-03.unrooted.nexus" , 100, False , False), # 100 trees (frequency column not reported by PAUP)
        ]
        splits_filename_template = "{stemname}.is-rooted-{is_rooted}.ignore-tree-weights-{ignore_weights}.burnin-{burnin}.splits.txt"
        for tree_filename, num_trees, treefile_is_rooted, treefile_is_weighted in sources:
            stemname = tree_filename
            for ignore_weights in (False, True, None):
                expected_is_rooted = treefile_is_rooted
                splits_filename = splits_filename_template.format(
                        stemname=stemname,
                        is_rooted=expected_is_rooted,
                        ignore_weights=ignore_weights,
                        burnin=0)
                self.check_splits_distribution(
                        tree_filename=tree_filename,
                        splits_filename=splits_filename,
                        is_rooted=treefile_is_rooted,
                        ignore_tree_weights=ignore_weights,
                        expected_num_trees=num_trees)

class SplitCountTest(ExtendedTestCase):

    @classmethod
    def setUpClass(cls):
        if sys.version_info.major < 3:
            cls.assertRaisesRegex = cls.assertRaisesRegexp

    def check_split_counting(self,
            tree_filename,
            test_as_rooted,
            parser_rooting_interpretation,
            test_ignore_tree_weights=False,
            dp_ignore_tree_weights=False,
            ):
        tree_filepath = pathmap.tree_source_path(tree_filename)
        paup_sd = paup.get_split_distribution(
                tree_filepaths=[tree_filepath],
                taxa_filepath=tree_filepath,
                is_rooted=test_as_rooted,
                ignore_tree_weights=test_ignore_tree_weights,
                burnin=0)
        taxon_namespace = paup_sd.taxon_namespace
        dp_sd = treesplit.SplitDistribution(
                taxon_namespace=taxon_namespace,
                )
        dp_sd.ignore_edge_lengths = True
        dp_sd.ignore_node_ages = True
        dp_sd.ignore_tree_weights = dp_ignore_tree_weights
        taxa_mask = taxon_namespace.all_taxa_bitmask()
        taxon_namespace.is_mutable = False
        trees = dendropy.TreeList.get_from_path(tree_filepath,
                "nexus",
                rooting=parser_rooting_interpretation,
                taxon_namespace=taxon_namespace)
        for tree in trees:
            self.assertIs(tree.taxon_namespace, taxon_namespace)
            self.assertIs(tree.taxon_namespace, dp_sd.taxon_namespace)
            dp_sd.count_splits_on_tree(
                    tree,
                    is_splits_encoded=False)
        self.assertEqual(dp_sd.total_trees_counted, paup_sd.total_trees_counted)
        taxa_mask = taxon_namespace.all_taxa_bitmask()
        for split in dp_sd.split_counts:
            if not treesplit.is_trivial_split(split, taxa_mask):
                self.assertIn(split, paup_sd.split_counts, "split not found")
                self.assertEqual(dp_sd.split_counts[split], paup_sd.split_counts[split], "incorrect split frequency")
                del paup_sd.split_counts[split]
        remaining_splits = list(paup_sd.split_counts.keys())
        for split in remaining_splits:
            if treesplit.is_trivial_split(split, taxa_mask):
                del paup_sd.split_counts[split]
        self.assertEqual(len(paup_sd.split_counts), 0)

    def test_basic_split_count_with_incorrect_rootings_raises_error(self):
        assertion_error_regexp1 = re.compile("(incorrect split frequency|split not found)")
        test_cases = (
            ('pythonidae.reference-trees.nexus', True, "force-unrooted", assertion_error_regexp1),
            ('feb032009.trees.nexus', False, "force-rooted", assertion_error_regexp1),
            )
        for test_case, test_as_rooted, parser_rooting_interpretation, assertion_error_regexp in test_cases:
            with self.assertRaisesRegex(AssertionError, assertion_error_regexp):
                self.check_split_counting(
                        test_case,
                        test_as_rooted=test_as_rooted,
                        parser_rooting_interpretation=parser_rooting_interpretation)

    def test_basic_split_count_with_incorrect_weight_treatment_raises_error(self):
        assertion_error_regexp1 = re.compile("incorrect split frequency")
        test_cases = (
                ("feb032009.trees.nexus", True),
                ("test_split_counting.rooted.weighted01.nexus", True),
                # ("test_split_counting.rooted.weighted02.nexus", True),
                # ("test_split_counting.rooted.weighted03.nexus", True),
                # ("test_split_counting.rooted.weighted04.nexus", True),
                # ("test_split_counting.rooted.weighted05.nexus", True),
                # ("test_split_counting.rooted.weighted06.nexus", True),
                # ("test_split_counting.rooted.weighted07.nexus", True),
                # ("test_split_counting.rooted.weighted08.nexus", True),
                # ("test_split_counting.rooted.weighted09.nexus", True),
                # ("test_split_counting.rooted.weighted10.nexus", True),
                # ("test_split_counting.unrooted.weighted01.nexus", False),
                # ("test_split_counting.unrooted.weighted02.nexus", False),
                # ("test_split_counting.unrooted.weighted03.nexus", False),
                # ("test_split_counting.unrooted.weighted04.nexus", False),
                # ("test_split_counting.unrooted.weighted05.nexus", False),
                # ("test_split_counting.unrooted.weighted06.nexus", False),
                # ("test_split_counting.unrooted.weighted07.nexus", False),
                # ("test_split_counting.unrooted.weighted08.nexus", False),
                # ("test_split_counting.unrooted.weighted09.nexus", False),
                # ("test_split_counting.unrooted.weighted10.nexus", False),
            )
        for test_case, test_as_rooted in test_cases:
            self.check_split_counting(
                    test_case,
                    test_as_rooted=test_as_rooted,
                    parser_rooting_interpretation="default-rooted",
                    test_ignore_tree_weights=False,
                    dp_ignore_tree_weights=False,
                    )
            with self.assertRaisesRegex(AssertionError, assertion_error_regexp1):
                self.check_split_counting(
                        test_case,
                        test_as_rooted=test_as_rooted,
                        parser_rooting_interpretation="default-rooted",
                        test_ignore_tree_weights=False,
                        dp_ignore_tree_weights=False,
                        )

    def test_basic_split_counting_under_different_rootings(self):
        test_cases = (
            'pythonidae.reference-trees.nexus',
            'feb032009.trees.nexus',
            'maj-rule-bug1.trees.nexus',
            'maj-rule-bug2.trees.nexus',
            )
        for is_rooted in (True, False):
            if is_rooted:
                rooting = "force-rooted"
            else:
                rooting = "force-unrooted"
            for test_case in test_cases:
                self.check_split_counting(
                        test_case,
                        test_as_rooted=is_rooted,
                        parser_rooting_interpretation=rooting)

class CladeMaskTest(unittest.TestCase):

    def runTest(self):
        tree_list = dendropy.TreeList.get_from_stream(
            StringIO("""((t5:0.161175,t6:0.161175):0.392293,((t4:0.104381,(t2:0.075411,t1:0.075411):1):0.065840,t3:0.170221):0.383247);"""),
            "newick")
        for i in tree_list:
            _LOG.debug(i._get_indented_form())
            treesplit.encode_splits(i)
            _LOG.debug(i._get_indented_form(splits=True))
            i._debug_check_tree(splits=True, logger_obj=_LOG)
        root1 = tree_list[0].seed_node
        root1e = root1.edge
        self.assertEqual(treesplit.split_to_list(root1e.split_bitmask), list(range(6)))
        self.assertEqual(treesplit.split_to_list(root1e.split_bitmask, one_based=True), list(range(1,7)))
        self.assertEqual(treesplit.split_to_list(root1e.split_bitmask, mask=21, one_based=True), [1, 3, 5])
        self.assertEqual(treesplit.split_to_list(root1e.split_bitmask, mask=21), [0, 2, 4])
        self.assertEqual(treesplit.count_bits(root1e.split_bitmask), 6)

        fc1 = root1.child_nodes()[0]
        fc1e = fc1.edge
        self.assertEqual(treesplit.split_to_list(fc1e.split_bitmask), [0, 1])
        self.assertEqual(treesplit.split_to_list(fc1e.split_bitmask, one_based=True), [1, 2])
        self.assertEqual(treesplit.split_to_list(fc1e.split_bitmask, mask=0x15, one_based=True), [1])
        self.assertEqual(treesplit.split_to_list(fc1e.split_bitmask, mask=0x15), [0])
        self.assertEqual(treesplit.count_bits(fc1e.split_bitmask), 2)

class CountBitsTest(unittest.TestCase):

    def runTest(self):
        self.assertEqual(treesplit.count_bits(21), 3)

class LowestBitTest(unittest.TestCase):

    def runTest(self):
        for n, expected in enumerate([0, 1, 2, 1, 4, 1, 2, 1, 8, 1, 2, 1, 4, 1, 2, 1, 16]):
            self.assertEqual(treesplit.lowest_bit_only(n), expected)

class IsTrivialTest(unittest.TestCase):

    def runTest(self):
        y = True
        n = False
        for i, r in enumerate([y, y, y, n, y, n, n, y, y, n, n, y, n, y, y, y, y, y, y, n, y, n, n, y, y, n, n, y, n, y, y, y, ]):
            self.assertEqual(r, treesplit.is_trivial_split(i, 0xF))
        for i, r in enumerate([y, y, y, n, y, n, n, n, y, n, n, n, n, n, n, y, y, n, n, n, n, n, n, y, n, n, n, y, n, y, y, y, ]):
            self.assertEqual(r, treesplit.is_trivial_split(i, 0x1F))
                              #0  1  2  3  4  5  6  7  8  9  0  1  2  3  4  5  6  7  8  9  0  1  2  3  4  5  6  7  8  9  0  1
        for i, r in enumerate([y, y, y, n, y, n, n, y, y, y, y, n, y, n, n, y, y, n, n, y, n, y, y, y, y, n, n, y, n, y, y, y, ]):
            self.assertEqual(r, treesplit.is_trivial_split(i, 0x17))

class IncompleteLeafSetSplitTest(unittest.TestCase):

    def check(self, title, src_prefix):
        tns = dendropy.TaxonNamespace()
        input_ds = dendropy.DataSet.get_from_path(
                src=pathmap.tree_source_path(src_prefix + ".dendropy-pruned.nex"),
                schema='nexus',
                attached_taxon_namespace=tns)
        input_taxa = input_ds.taxon_namespaces[0]
        output_ds = dendropy.DataSet.get_from_path(
                src=pathmap.tree_source_path(src_prefix + ".paup-pruned.nex"),
                schema='nexus',
                taxon_namespace=input_taxa)
        for set_idx, src_trees in enumerate(input_ds.tree_lists):
            src_trees = input_ds.tree_lists[set_idx]
            ref_trees = output_ds.tree_lists[set_idx]
            for tree_idx, src_tree in enumerate(src_trees):
                _LOG.debug("%s Set %d/%d, Tree %d/%d" % (title, set_idx+1, len(input_ds.tree_lists), tree_idx+1, len(src_trees)))
                ref_tree = ref_trees[tree_idx]
                # tree_dist = paup.symmetric_difference(src_tree, ref_tree)
                # d = src_tree.symmetric_difference(ref_tree)
                # if d > 0:
                #     print d
                self.assertEqual(treecompare.symmetric_difference(src_tree, ref_tree), 0)

    def testUnrooted(self):
        self.check("Unrooted", "incomplete_leaves_unrooted")

    def testRooted(self):
        self.check("Rooted", "incomplete_leaves_rooted")

    def testPrunedThenEncoding(self):
        inp = StringIO('''(a,b,c,(d,e));
        (b,d,(c,e));''')
        first, second = dendropy.TreeList.get_from_stream(inp, schema='newick')
        # prune tree 1 to have the same leaf set as tree 2.
        #   this removes the first taxon in the taxon list "A"
        retain_list = set([node.taxon for node in second.leaf_nodes()])
        exclude_list = [node for node in first.leaf_nodes() if node.taxon not in retain_list]
        for nd in exclude_list:
            first.prune_subtree(nd)
        # the trees are now (b,c,(d,e)) and (b,d,(c,e)) so the symmetric diff is 2
        self.assertEqual(2, treecompare.symmetric_difference(first, second))

class TestTreeSplitSupportCredibilityScoring(unittest.TestCase):

    def setUp(self):
        self.trees = dendropy.TreeList.get_from_path(
                pathmap.tree_source_path("issue_mth_2009-02-03.rooted.nexus"),
                "nexus")
        self.split_distribution = treesplit.SplitDistribution(taxon_namespace=self.trees.taxon_namespace)
        for tree in self.trees:
            self.split_distribution.count_splits_on_tree(
                    tree,
                    is_splits_encoded=False)

    def test_product_of_split_support_on_tree(self):
        t1 = self.trees[70]
        self.assertAlmostEqual(
                self.split_distribution.product_of_split_support_on_tree(t1),
                -33.888380488585284)

    def test_sum_of_split_support_on_tree(self):
        t1 = self.trees[73]
        self.assertAlmostEqual(
                self.split_distribution.sum_of_split_support_on_tree(t1),
                30.89000000000001)

    def test_sum_of_split_support_on_tree2(self):
        t1 = self.trees[73]
        self.assertAlmostEqual(
                self.split_distribution.sum_of_split_support_on_tree(t1, include_external_splits=True),
                30.89000000000001 + len(self.trees.taxon_namespace))

if __name__ == "__main__":
    if paup.DENDROPY_PAUP_INTEROPERABILITY:
        unittest.main()
    else:
        _LOG.warn("PAUP interoperability not available: skipping split counting tests")
