#! /usr/bin/env python

##############################################################################
##  DendroPy Phylogenetic Computing Library.
##
##  Copyright 2010 Jeet Sukumaran and Mark T. Holder.
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
CLI wrapper for tree summarization.
"""

import os
import sys
import re
from optparse import OptionParser
from optparse import OptionGroup
if not (sys.version_info.major >= 3 and sys.version_info.minor >= 4):
    from dendropy.utility.filesys import pre_py34_open as open
from decimal import Decimal

import datetime
import time
import socket
try:
    import getpass
except:
    pass
import platform
try:
    try:
        # Python 3
        import queue
    except ImportError:
        # Python 2.7
        import Queue as queue
    import multiprocessing
    _MP = True
except ImportError:
    # Python < 2.7
    _MP = False

import dendropy
from dendropy.calculate import treesplit
from dendropy.calculate import treesum
from dendropy.utility.messaging import ConsoleMessenger
from dendropy.utility.cli import confirm_overwrite, show_splash
from dendropy.mathlib import statistics

_program_name = "SumTrees"
_program_subtitle = "Phylogenetic Tree Split Support Summarization"
_program_date = "May 05 2011"
_program_version = "Version 3.3.1 (%s)" % _program_date
_program_author = "Jeet Sukumaran and Mark T. Holder"
_program_contact = "jeetsukumaran@gmail.com"
_program_copyright = "Copyright (C) 2008 Jeet Sukumaran.\n" \
                 "License GPLv3+: GNU GPL version 3 or later.\n" \
                 "This is free software: you are free to change\nand redistribute it. " \
                 "There is NO WARRANTY,\nto the extent permitted by law."

GREATER_THAN_50_PERCENT = float(Decimal(0.5).next_plus())

if _MP:
    class SplitCountingWorker(multiprocessing.Process):

        def __init__(self,
                work_queue,
                result_split_dist_queue,
                result_topology_hash_map_queue,
                schema,
                taxon_labels,
                rooting_interpretation,
                ignore_node_ages,
                ultrametricity_precision,
                calc_tree_probs,
                weighted_trees,
                tree_offset,
                process_idx,
                messenger,
                messenger_lock,
                log_frequency=1000):
            multiprocessing.Process.__init__(self)
            self.work_queue = work_queue
            self.result_split_dist_queue = result_split_dist_queue
            self.result_topology_hash_map_queue = result_topology_hash_map_queue
            self.schema = schema
            self.taxon_labels = list(taxon_labels)
            self.taxon_namespace = dendropy.TaxonNamespace(self.taxon_labels)
            self.split_distribution = treesplit.SplitDistribution(taxon_namespace=self.taxon_namespace)
            self.split_distribution.ignore_node_ages = ignore_node_ages
            self.split_distribution.ultrametricity_precision = ultrametricity_precision
            self.rooting_interpretation = rooting_interpretation
            self.calc_tree_probs = calc_tree_probs
            self.topology_counter = treesum.TopologyCounter()
            self.weighted_trees = weighted_trees
            self.tree_offset = tree_offset
            self.process_idx = process_idx
            self.messenger = messenger
            self.messenger_lock = messenger_lock
            self.log_frequency = log_frequency
            self.kill_received = False

        def send_message(self, msg, level, wrap=True):
            if self.messenger is None:
                return
            if self.messenger.messaging_level > level or self.messenger.silent:
                return
            msg = "Thread %d: %s" % (self.process_idx+1, msg)
            self.messenger_lock.acquire()
            try:
                self.messenger.log(msg, level=level, wrap=wrap)
            finally:
                self.messenger_lock.release()

        def send_info(self, msg, wrap=True):
            self.send_message(msg, ConsoleMessenger.INFO_MESSAGING_LEVEL, wrap=wrap)

        def send_warning(self, msg, wrap=True):
            self.send_message(msg, ConsoleMessenger.WARNING_MESSAGING_LEVEL, wrap=wrap)

        def send_error(self, msg, wrap=True):
            self.send_message(msg, ConsoleMessenger.ERROR_MESSAGING_LEVEL, wrap=wrap)

        def run(self):
            while not self.kill_received:
                try:
                    source = self.work_queue.get_nowait()
                except queue.Empty:
                    break
                self.send_info("Received task: '%s'." % source, wrap=False)
                fsrc = open(source, "rU")
                for tidx, tree in enumerate(dendropy.Tree.yield_from_files(
                        [fsrc],
                        schema=self.schema,
                        taxon_namespace=self.taxon_namespace,
                        rooting=self.rooting_interpretation,
                        store_tree_weights=self.weighted_trees)):
                    assert tree.taxon_namespace is self.taxon_namespace
                    if tidx >= self.tree_offset:
                        if (self.log_frequency == 1) or (tidx > 0 and self.log_frequency > 0 and tidx % self.log_frequency == 0):
                            self.send_info("(processing) '%s': tree at offset %d" % (source, tidx), wrap=False)
                        self.split_distribution.count_splits_on_tree(tree, is_splits_encoded=False)
                        if self.calc_tree_probs:
                            self.topology_counter.count(tree,
                                    is_splits_encoded=True)
                    else:
                        if (self.log_frequency == 1) or (tidx > 0 and self.log_frequency > 0 and tidx % self.log_frequency == 0):
                            self.send_info("(processing) '%s': tree at offset %d (skipping)" % (source, tidx), wrap=False)
                    if self.kill_received:
                        break
                if self.kill_received:
                    break
                self.send_info("Completed task: '%s'." % (source), wrap=False)
            if self.kill_received:
                self.send_warning("Terminating in response to kill request.")
            else:
                self.result_split_dist_queue.put(self.split_distribution)
                self.result_topology_hash_map_queue.put(self.topology_counter.topology_hash_map)

def discover_taxa(treefile, schema):
    """
    Reads first tree in treefile, and assumes that is sufficient to populate a
    taxon set object fully, which it then returns.
    """
    if isinstance(treefile, str):
        tdf = open(treefile, "rU")
    else:
        tdf = treefile
    tt = None
    for tree in dendropy.Tree.yield_from_files([tdf], schema=schema):
        tt = tree
        break
    taxon_namespace = tt.taxon_namespace
    return taxon_namespace

def process_sources_parallel(
        num_processes,
        support_filepaths,
        schema,
        rooting_interpretation,
        ignore_node_ages,
        ultrametricity_precision,
        calc_tree_probs,
        weighted_trees,
        tree_offset,
        log_frequency,
        messenger):
    """
    Returns a SplitDistribution object summarizing all trees found in
    `support_filepaths`.
    """

    # describe
    messenger.info("Running in multiprocessing mode (up to %d processes)." % num_processes)
    messenger.info("%d sources to be processed." % (len(support_filepaths)))

    # pre-discover taxa
    tdfpath = support_filepaths[0]
    messenger.info("Pre-loading taxa based on '%s' ..." % tdfpath)
    taxon_namespace = discover_taxa(tdfpath, schema)
    taxon_labels = [t.label for t in taxon_namespace]
    messenger.info("Found %d taxa: [%s]" % (len(taxon_labels), (', '.join(["'%s'" % t for t in taxon_labels]))))

    # load up queue
    messenger.info("Creating work queue ...")
    work_queue = multiprocessing.Queue()
    for f in support_filepaths:
        work_queue.put(f)

    # launch processes
    messenger.info("Launching worker processes ...")
    result_split_dist_queue = multiprocessing.Queue()
    result_topology_hash_map_queue = multiprocessing.Queue()
    messenger_lock = multiprocessing.Lock()
    for idx in range(num_processes):
        sct = SplitCountingWorker(work_queue,
                result_split_dist_queue=result_split_dist_queue,
                result_topology_hash_map_queue=result_topology_hash_map_queue,
                schema=schema,
                taxon_labels=taxon_labels,
                rooting_interpretation=rooting_interpretation,
                ignore_node_ages=ignore_node_ages,
                ultrametricity_precision=ultrametricity_precision,
                calc_tree_probs=calc_tree_probs,
                weighted_trees=weighted_trees,
                tree_offset=tree_offset,
                process_idx=idx,
                messenger=messenger,
                messenger_lock=messenger_lock,
                log_frequency=log_frequency)
        sct.start()

    # collate results
    result_count = 0
    split_distribution = treesplit.SplitDistribution(taxon_namespace=taxon_namespace)
    split_distribution.ignore_node_ages = ignore_node_ages
    topology_counter = treesum.TopologyCounter()
    while result_count < num_processes:
        result_split_dist = result_split_dist_queue.get()
        split_distribution.update(result_split_dist)
        result_topology_hash_map = result_topology_hash_map_queue.get()
        topology_counter.update_topology_hash_map(result_topology_hash_map)
        result_count += 1
    messenger.info("Recovered results from all worker processes.")
    return split_distribution, topology_counter

def process_sources_serial(
        support_filepaths,
        schema,
        rooting_interpretation,
        ignore_node_ages,
        ultrametricity_precision,
        calc_tree_probs,
        weighted_trees,
        tree_offset,
        log_frequency,
        messenger):
    """
    Returns a SplitDistribution object summarizing all trees found in
    `support_filepaths`.
    """
    messenger.info("Running in serial mode.")
    taxon_namespace = dendropy.TaxonNamespace()
    split_distribution = treesplit.SplitDistribution(taxon_namespace=taxon_namespace)
    split_distribution.ignore_node_ages = ignore_node_ages
    split_distribution.ultrametricity_precision = ultrametricity_precision
    topology_counter = treesum.TopologyCounter()
    if support_filepaths is None or len(support_filepaths) == 0:
        messenger.info("Reading trees from standard input.")
        srcs = [sys.stdin]
    else:
        messenger.info("%d source(s) to be processed." % len(support_filepaths))

        # do not want to have all files open at the same time
        #srcs = [open(f, "rU") for f in support_filepaths]

        # store filepaths, to open individually in loop
        srcs = support_filepaths
    tree_yielder = dendropy.Tree.yield_from_files(
            srcs,
            schema=schema,
            taxon_namespace=taxon_namespace,
            store_tree_weights=weighted_trees,
            rooting=rooting_interpretation)
    current_index = None
    for tidx, tree in enumerate(tree_yielder):
        current_yielder_index = tree_yielder.current_file_index
        if current_yielder_index != current_index:
            current_index = current_yielder_index
            name = tree_yielder.current_file_name
            if name is None:
                name = "<stdin>"
            messenger.info("Processing %d of %d: '%s'" % (current_index+1, len(srcs), name), wrap=False)
        if tidx >= tree_offset:
            if (log_frequency == 1) or (tidx > 0 and log_frequency > 0 and tidx % log_frequency == 0):
                messenger.info("(processing) '%s': tree at offset %d" % (name, tidx), wrap=False)
            split_distribution.count_splits_on_tree(tree, is_splits_encoded=False)
            if len(split_distribution.tree_rooting_types_counted) > 1:
                mixed_tree_rootings_in_source_error(messenger)
            topology_counter.count(tree, is_splits_encoded=True)
        else:
            if (log_frequency == 1) or (tidx > 0 and log_frequency > 0 and tidx % log_frequency == 0):
                messenger.info("(processing) '%s': tree at offset %d (skipping)" % (name, tidx), wrap=False)

    messenger.info("Serial processing of %d source(s) completed." % len(srcs))
    return split_distribution, topology_counter

def mixed_tree_rootings_in_source_error(messenger):
    messenger.error(
            "Both rooted as well as unrooted trees found in input trees."
            " Support values are meaningless. Rerun SumTrees using the"
            " '--rooted' or the '--unrooted' option to force a consistent"
            " rooting state for the support trees.")
    sys.exit(1)

def main_cli():

    description =  "%s %s %s" % (_program_name, _program_version, _program_subtitle)
    usage = "%prog [options] TREES-FILE [TREES-FILE [TREES-FILE [...]]"

    parser = OptionParser(usage=usage, add_help_option=True, version = _program_version, description=description)

    sum_tree_optgroup = OptionGroup(parser, "Source Treatment Options")
    parser.add_option_group(sum_tree_optgroup)
    sum_tree_optgroup.add_option("-b", "--burnin",
            action="store",
            dest="burnin",
            type="int",
            default=0,
            help='number of trees to skip from the beginning of *each tree file* when counting support [default=%default]')

    source_tree_optgroup = OptionGroup(parser, "Source Tree Options")
    parser.add_option_group(source_tree_optgroup)
    source_tree_optgroup.add_option("--rooted",
            action="store_true",
            dest="rooted_trees",
            default=None,
            help="treat trees as rooted")
    source_tree_optgroup.add_option("--unrooted",
            action="store_false",
            dest="rooted_trees",
            default=None,
            help="treat trees as unrooted")
    source_tree_optgroup.add_option("--ultrametric",
            action="store_true",
            dest="ultrametric_trees",
            default=False,
            help="assume trees are ultrametric (implies '--rooted' ; will result in node ages being summarized; will result in error if trees are not ultrametric)")
    source_tree_optgroup.add_option("--weighted-trees",
            action="store_true",
            dest="weighted_trees",
            default=False,
            help="use weights of trees as indicated by '[&W m/n]' comment to weight contribution of splits found on each tree to overall split frequencies")
    source_tree_optgroup.add_option("--from-newick-stream",
            action="store_true",
            dest="from_newick_stream",
            default=False,
            help="support trees will be streamed in newick format")
    source_tree_optgroup.add_option("--from-nexus-stream",
            action="store_true",
            dest="from_nexus_stream",
            default=False,
            help="support trees will be streamed in NEXUS format")

    target_tree_optgroup = OptionGroup(parser, 'Target Tree Options')
    parser.add_option_group(target_tree_optgroup)
    target_tree_optgroup.add_option("-t","--target",
            dest="target_tree_filepath",
            default=None,
            help="path to optional target, model or best topology tree file (Newick or NEXUS format) "
            + "to which support will be mapped; "
            + "if not given, then a majority-rule clade consensus tree will be constructed based on the "
            + "all the trees given in the support tree files (except for those discarded as burn-ins), "
            + "and this will be used as the target tree")
    target_tree_optgroup.add_option("-f", "--min-clade-freq",
            dest="min_clade_freq",
            type="float",
            default=GREATER_THAN_50_PERCENT,
            metavar="#.##",
            help="minimum frequency or probability for a clade or a split to be "\
                    + "included in the consensus tree, if used [default: > 0.05]")
    target_tree_optgroup.add_option("--root-target-at-midpoint",
            action="store_true",
            dest="root_target_at_midpoint",
            default=None,
            help="Explicitly root target tree(s) to be rooted at midpoint")
    # target_tree_optgroup.add_option("--root-target-at-outgroup",
    #         dest="root_target_at_outgroup",
    #         default=None,
    #         help="Explicitly root target tree(s) using specified outgroup")

    support_summarization_optgroup = OptionGroup(parser, "Support Summarization Options")
    parser.add_option_group(support_summarization_optgroup)
    support_summarization_optgroup.add_option("-l","--support-as-labels",
            action="store_const",
            dest="support_annotation_target",
            default=1,
            const=1,
            help="in addition to node metadata, indicate branch support as internal node labels [default]")
    support_summarization_optgroup.add_option("-v","--support-as-lengths",
            action="store_const",
            dest="support_annotation_target",
            default=1,
            const=2,
            help="in addition to node metadata, indicate branch support as branch lengths")
    support_summarization_optgroup.add_option("-x","--no-support",
            action="store_const",
            dest="support_annotation_target",
            default=1,
            const=0,
            help="""\
do not indicate support with internal node labels or edge lengths
(support will still be indicated as node metadata unless
'--no-summary-metadata' is specified)""")
    support_summarization_optgroup.add_option("-p", "--percentages",
            action="store_true",
            dest="support_as_percentages",
            default=False,
            help="indicate branch support as percentages (otherwise, will report as proportions by default)")
    support_summarization_optgroup.add_option("-d", "--decimals",
            dest="support_label_decimals",
            type="int",
            metavar="#",
            default=8,
            help="number of decimal places in indication of support values [default=%default]")

    edge_summarization_optgroup = OptionGroup(parser, "Edge Length Summarization Options")
    parser.add_option_group(edge_summarization_optgroup)
    edge_summarization_choices = ["mean-length", "median-length", "mean-age", "median-age", "keep", "unweighted"]
    edge_summarization_optgroup.add_option("-e", "--edges",
            type="choice",
            dest="edge_summarization",
            metavar="<%s>" % ("|".join(edge_summarization_choices)),
            choices=edge_summarization_choices,
            default=None,
            help="""\
set edge lengths of target tree(s) to mean/median lengths/ages of
corresponding splits or edges of input trees (note that using 'mean-age' or
'median-age' require rooted ultrametric input trees, and will behave as
if '--ultrametric' and '--with-node-ages' are specified");
default is to 'keep' if target trees are specified
(i.e., target trees will have their branch lengths preserved by default),
'median-age' if no target trees are specified but the '--ultrametric' directive is given
(a consensus tree should be constructed to summarize support and input trees are ultrametric),
and 'mean-length' if no target trees are specified and the '--ultrametric' directive is *not* given
(a consensus tree should be constructed to summarize support and input trees are *not* assumed to be ultrametric),
""")
    edge_summarization_optgroup.add_option("--collapse-negative-edges",
            action="store_true",
            dest="collapse_negative_edges",
            default=False,
            help="(if setting edge lengths) force parent node ages to be at least as old as its oldest child when summarizing node ages")

    other_summarization_optgroup = OptionGroup(parser, "Other Summarization Options")
    parser.add_option_group(other_summarization_optgroup)
    #other_summarization_optgroup.add_option("--with-node-ages",
    #        action="store_true",
    #        dest="calc_node_ages",
    #        default=None,
    #        help="summarize node ages as well as edge lengths (implies '--rooted' and '--ultrametric'; automatically enabled if '--ultrametric' is specified; will result in error if trees are not ultrametric)")
    other_summarization_optgroup.add_option("--trprobs", "--calc-tree-probabilities",
            dest="trprobs_filepath",
            default=None,
            metavar="FILEPATH",
            help="if specified, a file listing tree (topologies) and the " \
                    + "frequencies of their occurrences will be saved to FILEPATH")
    other_summarization_optgroup.add_option("--extract-edges",
            dest="split_edge_map_filepath",
            default=None,
            metavar="FILEPATH",
            help="if specified, a tab-delimited file of splits and their edge " \
                    + "lengths across input trees will be saved to FILEPATH")
    other_summarization_optgroup.add_option("--no-node-ages",
            action="store_false",
            dest="calc_node_ages",
            default=None,
            help="do not calculate/summarize node ages, even if '--ultrametric' is specified")
    other_summarization_optgroup.add_option("--no-summary-metadata",
            action="store_true",
            dest="suppress_summary_metadata",
            default=False,
            help="do not annotate nodes with ranges, 5%/95 quartiles, 95% HPD's etc. of edge lengths and node ages")
    other_summarization_optgroup.add_option("--ultrametricity-precision",
            default=0.0000001,
            type="float",
            help="precision when checking ultrametricity")

    output_filepath_optgroup = OptionGroup(parser, "Output File Options")
    parser.add_option_group(output_filepath_optgroup)
    output_filepath_optgroup.add_option("-o","--output",
            dest="output_filepath",
            default=None,
            help="path to output file (if not given, will print to standard output)")
    output_filepath_optgroup.add_option("--no-taxa-block",
            action="store_false",
            dest="include_taxa_block",
            default=True,
            help="do not include a taxa block in the output treefile (otherwise will create taxa block by default)")
    output_filepath_optgroup.add_option("--no-meta-comments",
            action="store_false",
            dest="include_meta_comments",
            default=True,
            help="do not include initial file comment annotating details of scoring operation")
    output_filepath_optgroup.add_option("-c", "--additional-comments",
            action="store",
            dest="additional_comments",
            default=None,
            help="additional comments to be added to the summary file")
    output_filepath_optgroup.add_option("--to-newick",
            action="store_true",
            dest="to_newick_format",
            default=False,
            help="save results in NEWICK (PHYLIP) format (default is to save in NEXUS format)")
    output_filepath_optgroup.add_option("--to-phylip",
            action="store_true",
            dest="to_newick_format",
            default=False,
            help="same as --newick")
    output_filepath_optgroup.add_option("-r", "--replace",
            action="store_true",
            dest="replace",
            default=False,
            help="replace/overwrite output file without asking if it already exists ")

    run_optgroup = OptionGroup(parser, "Program Run Options")
    parser.add_option_group(run_optgroup)
    if _MP:
        run_optgroup.add_option("-m", "--multiprocessing",
                action="store",
                dest="multiprocess",
                metavar="NUM-PROCESSES",
                default=None,
                help="run in parallel mode with up to a maximum of NUM-PROCESSES processes " \
                        + "(specify '*' to run in as many processes as there are cores on the "\
                        + "local machine)")

    run_optgroup.add_option("-g", "--log-frequency",
            type="int",
            metavar="LOG-FREQUENCY",
            dest="log_frequency",
            default=500,
            help="tree processing progress logging frequency (default=%default; set to 0 to suppress)")
    run_optgroup.add_option("-q", "--quiet",
            action="store_true",
            dest="quiet",
            default=False,
            help="suppress ALL logging, progress and feedback messages")
    run_optgroup.add_option("--ignore-missing-support",
            action="store_true",
            dest="ignore_missing_support",
            default=False,
            help="ignore missing support tree files (at least one must exist!)")
    run_optgroup.add_option("--ignore-missing-target",
            action="store_true",
            dest="ignore_missing_target",
            default=False,
            help="ignore missing target tree file (will construct majority rule consensus tree if missing)")

    (opts, args) = parser.parse_args()
    if opts.quiet:
        messaging_level = ConsoleMessenger.ERROR_MESSAGING_LEVEL
    else:
        messaging_level = ConsoleMessenger.INFO_MESSAGING_LEVEL
    messenger = ConsoleMessenger(name="SumTrees", messaging_level=messaging_level)

    # splash
    if not opts.quiet:
        show_splash(prog_name=_program_name,
                prog_subtitle=_program_subtitle,
                prog_version=_program_version,
                prog_author=_program_author,
                prog_copyright=_program_copyright,
                dest=sys.stderr,
                extended=False)

    ###################################################
    # Support file idiot checking

    support_filepaths = []
    if len(args) > 0:
        for fpath in args:
            fpath = os.path.expanduser(os.path.expandvars(fpath))
            if not os.path.exists(fpath):
                if opts.ignore_missing_support:
                    messenger.warning("Support file not found: '%s'" % fpath)
                else:
                    messenger.error("Terminating due to missing support files. "
                           + "Use the '--ignore-missing-support' option to continue even "
                           + "if some files are missing.")
                    sys.exit(1)
            else:
                support_filepaths.append(fpath)
        if len(support_filepaths) == 0:
            messenger.error("No valid sources of input trees specified. "
                    + "Please provide the path to at least one (valid and existing) file "
                    + "containing tree samples to summarize.")
            sys.exit(1)
    else:
        if not opts.from_newick_stream and not opts.from_nexus_stream:
            messenger.info("No sources of input trees specified. "
                    + "Please provide the path to at least one (valid and existing) file "
                    + "containing tree samples to summarize. See '--help' for other options.")
            sys.exit(1)

    ###################################################
    # Lots of other idiot-checking ...

    # target tree
    if opts.target_tree_filepath is not None:
        target_tree_filepath = os.path.expanduser(os.path.expandvars(opts.target_tree_filepath))
        if not os.path.exists(target_tree_filepath):
            if opts.ignore_missing_target:
                if not opts.quiet:
                    messenger.warning("Target tree file not found: '%s': using majority-rule consensus tree instead." % target_tree_filepath)
                target_tree_filepath = None
            else:
                messenger.error("Target tree file not found: '%s'" % target_tree_filepath)
                sys.exit(1)
    else:
        target_tree_filepath = None

    ### TODO: idiot-check edge length summarization
    # edge lengths
    if opts.edge_summarization:
        opts.edge_summarization = opts.edge_summarization.lower()
        if opts.edge_summarization not in edge_summarization_choices:
            messenger.error("'%s' is not a valid edge summarization choice; must be one of: %s" % (opts.edge_summarization, edge_summarization_choices))
            sys.exit(1)
    if opts.edge_summarization == "mean-age" or opts.edge_summarization == "median-age":
        opts.ultrametric_trees = True
        opts.rooted_trees = True
        if opts.calc_node_ages is None:
            opts.calc_node_ages = True
    else:
        if opts.ultrametric_trees:
            opts.rooted_trees = True
            if opts.calc_node_ages is None:
                opts.calc_node_ages = True
        else:
            if opts.calc_node_ages is True:
                opts.ultrametric_trees = True
                opts.rooted_trees = True
            else:
                opts.calc_node_ages = False

    if opts.rooted_trees is True:
        rooting_interpretation = "force-rooted"
    elif opts.rooted_trees is False:
        rooting_interpretation = "force-unrooted"
    else:
        rooting_interpretation = "default-unrooted"

    # output
    if opts.output_filepath is None:
        output_dest = sys.stdout
    else:
        output_fpath = os.path.expanduser(os.path.expandvars(opts.output_filepath))
        if confirm_overwrite(filepath=output_fpath, replace_without_asking=opts.replace):
            output_dest = open(output_fpath, "w")
        else:
            sys.exit(1)

    if opts.trprobs_filepath:
        trprobs_filepath = os.path.expanduser(os.path.expandvars(opts.trprobs_filepath))
        if confirm_overwrite(filepath=trprobs_filepath, replace_without_asking=opts.replace):
            trprobs_dest = open(trprobs_filepath, "w")
        else:
            sys.exit(1)
        opts.calc_tree_probs = True
    else:
        trprobs_dest = None
        opts.calc_tree_probs = False

    if opts.split_edge_map_filepath:
        split_edge_map_filepath = os.path.expanduser(os.path.expandvars(opts.split_edge_map_filepath))
        if confirm_overwrite(filepath=split_edge_map_filepath, replace_without_asking=opts.replace):
            split_edge_map_dest = open(split_edge_map_filepath, "w")
        else:
            sys.exit(1)
    else:
        split_edge_map_dest = None

    if opts.from_newick_stream:
        schema = "newick"
    elif opts.from_nexus_stream:
        schema = "nexus"
    else:
        schema = 'nexus/newick'

    ###################################################
    # Main work begins here: Count the splits

    start_time = datetime.datetime.now()
    master_split_distribution = None
    if (support_filepaths is not None and len(support_filepaths) > 1) \
            and _MP \
            and opts.multiprocess:
        if opts.multiprocess is not None:
            if opts.multiprocess == "*":
                num_processes = multiprocessing.cpu_count()
            elif  opts.multiprocess == "@":
                num_processes = len(support_filepaths)
            else:
                try:
                    num_processes = int(opts.multiprocess)
                except ValueError:
                    messenger.error("'%s' is not a valid number of processes (must be a positive integer)." % opts.multiprocess)
                    sys.exit(1)
            if num_processes <= 0:
                messenger.error("Maximum number of processes set to %d: cannot run SumTrees with less than 1 process" % num_processes)
                sys.exit(1)
            if num_processes == 1:
                messenger.warning("Running in parallel processing mode but limited to only 1 process: probably more efficient to run in serial mode!")

        master_split_distribution, master_topology_counter = process_sources_parallel(
                num_processes=num_processes,
                support_filepaths=support_filepaths,
                schema=schema,
                rooting_interpretation=rooting_interpretation,
                ignore_node_ages=not opts.calc_node_ages,
                ultrametricity_precision=opts.ultrametricity_precision,
                calc_tree_probs=opts.calc_tree_probs,
                weighted_trees=opts.weighted_trees,
                tree_offset=opts.burnin,
                log_frequency=opts.log_frequency,
                messenger=messenger)
    else:
        if (_MP and opts.multiprocess is not None and len(support_filepaths) == 1):
            messenger.warning("Parallel processing mode requested but only one source specified: defaulting to serial mode.")
        if opts.from_newick_stream or opts.from_nexus_stream:
            support_filepaths = None
        master_split_distribution, master_topology_counter = process_sources_serial(
                support_filepaths=support_filepaths,
                schema=schema,
                rooting_interpretation=rooting_interpretation,
                ignore_node_ages=not opts.calc_node_ages,
                ultrametricity_precision=opts.ultrametricity_precision,
                calc_tree_probs=opts.calc_tree_probs,
                weighted_trees=opts.weighted_trees,
                tree_offset=opts.burnin,
                log_frequency=opts.log_frequency,
                messenger=messenger)

    ###################################################
    # Compose post-counting report

    # if not splits counted or the taxon set was not populated for any reason,
    # we just produce an empty block so we don't crash as we report nothing of interest
    if master_split_distribution.taxon_namespace is None:
        assert(master_split_distribution.total_trees_counted == 0)
        master_split_distribution.taxon_namespace = dendropy.TaxonNamespace()

    # taxon set to handle target trees
    master_taxon_namespace = master_split_distribution.taxon_namespace

    report = []
    report.append("%d trees considered in total for split support assessment." % (master_split_distribution.total_trees_counted))
    if opts.rooted_trees is None:
        report.append("Tree rooting as given by tree statement (defaults to unrooted).")
    elif opts.rooted_trees:
        report.append("Trees treated as rooted.")
    else:
        report.append("Trees treated as unrooted.")
    if opts.ultrametric_trees:
        report.append("Trees are expected to be ultrametric.")
    if opts.weighted_trees:
        report.append("Trees treated as weighted (default weight = 1.0).")
    else:
        report.append("Trees treated as unweighted.")
    if master_split_distribution.is_mixed_rootings_counted():
        mixed_tree_rootings_in_source_error(messenger)
    n_taxa = len(master_taxon_namespace)
    report.append("%d unique taxa across all trees." % n_taxa)
    num_splits, num_unique_splits, num_nt_splits, num_nt_unique_splits = master_split_distribution.splits_considered()
    report.append("%d unique splits out of %d total splits counted." % (num_unique_splits, num_splits))
    report.append("%d unique non-trivial splits out of %d total non-trivial splits counted." % (num_nt_unique_splits, num_nt_splits))

    comments = []
    comments.extend(report)
    messenger.info("Split counting completed:")
    messenger.info_lines(report, prefix=" - ")

    ###################################################
    #  Formatting

    if not opts.support_as_percentages and opts.support_label_decimals < 2:
        messenger.warning("Reporting support by proportions require that support will be reported to at least 2 decimal places")
        opts.support_label_decimals = 2

    real_value_format_specifier = None

    ###################################################
    #  Target tree and mapping

    tsum = treesum.TreeSummarizer()
    tsum.add_node_metadata = not opts.suppress_summary_metadata
    if opts.support_annotation_target == 1:
        tsum.support_as_labels = True
        tsum.support_as_edge_lengths = False
        support_show = "indicated by node labels"
        if tsum.add_node_metadata:
            support_show += " and node metadata"
    elif opts.support_annotation_target == 2:
        tsum.support_as_labels = False
        tsum.support_as_edge_lengths = True
        support_show = "indicated by branch lengths"
        if tsum.add_node_metadata:
            support_show += " and node metadata"
    elif opts.support_annotation_target == 0:
        tsum.support_as_labels = False
        tsum.support_as_edge_lengths = False
        if tsum.add_node_metadata:
            support_show = "indicated by node metadata (only)"
        else:
            support_show = "not indicated"
    else:
        raise Exception("Unexpected value for support annotation target: %s" % opts.support_annotation_target)
    tsum.support_as_percentages = opts.support_as_percentages
    tsum.support_label_decimals = opts.support_label_decimals
    tsum.weighted_splits = opts.weighted_trees

    if opts.support_as_percentages:
        support_units = "Percentage"
    else:
        support_units = "Proportion (frequency or probability)"
    support_summarization = "%s of support for each split %s" % (support_units, support_show)

    tt_trees = []
    support_comment_pattern = re.compile(r'support\s*=\s*[0-9.eE-]+,?', re.I)
    if target_tree_filepath is not None:
        messenger.info("Mapping support to target tree ...")
        # if adding node metadata, we extract it from the target tree first
        for tree in dendropy.Tree.yield_from_files([target_tree_filepath],
                schema="nexus/newick",
                taxon_namespace=master_taxon_namespace,
                rooting=rooting_interpretation,
                extract_comment_metadata=tsum.add_node_metadata):
            if opts.root_target_at_midpoint:
                tree.root_at_midpoint(splits=True)
            # elif opts.root_target_at_outgroup:
            #     comments.append("Target tree(s) rooted at midpoint.")
            if master_split_distribution.is_all_counted_trees_rooted:
                if not tree.is_rooted:
                    messenger.error("Support trees are rooted, but target tree is unrooted. Root target tree(s) and re-run, or run using the '--root-target' flag.")
                    sys.exit(1)
            elif tree.is_rooted:
                messenger.error("Support trees are unrooted, but target tree is rooted. Ensure target tree(s) are all of the same rooting state as the input support trees.")
                sys.exit(1)
            # strip out existing support statement
            # if tsum.add_node_metadata:
            #     for nd in tree.postorder_node_iter():
            #         for nd_comment_idx, comment in enumerate(nd.comments):
            #             nd.comments[nd_comment_idx] = support_comment_pattern.sub("", nd.comments[nd_comment_idx])
            stree = tsum.map_split_support_to_tree(tree, master_split_distribution)
            tt_trees.append(stree)
        messenger.info("Parsed '%s': %d tree(s) in file" % (target_tree_filepath, len(tt_trees)))
        comments.append("Split support mapped to trees in:")
        comments.append("  - '%s' (%d trees)" % (os.path.abspath(target_tree_filepath), len(tt_trees)))
        if opts.root_target_at_midpoint:
            comments.append("Target tree(s) rooted using outgroup: %s." % opts.outgroup)
        # elif opts.root_target_at_outgroup:
        #     comments.append("Target tree(s) rooted at midpoint.")
        comments.append(support_summarization + '.')
    else:
        messenger.info("Constructing clade consensus tree ...")
        if opts.min_clade_freq > 1.0:
            messenger.warning("Maximum frequency threshold for clade inclusion is 1.0: reset to 1.0.")
            min_freq = 1.0
        else:
            min_freq = opts.min_clade_freq
        stree = tsum.tree_from_splits(master_split_distribution,
                min_freq=min_freq,
                include_edge_lengths=False)
                #include_edge_lengths=not opts.no_branch_lengths)
        report.append("Consensus tree (%f clade frequency threshold) constructed from splits." % min_freq)
        tt_trees.append(stree)
        if opts.root_target_at_midpoint:
            report.append("Consensus tree rooted using outgroup: %s." % opts.outgroup)
            stree.reroot_at_midpoint(update_splits=True)
        # elif opts.root_target_at_outgroup:
        #     raise NotImplementedError
        #     report.append("Consensus tree rooted at midpoint.")
        report.append(support_summarization + ".")
        messenger.info_lines(report)
        comments.extend(report)

    if not opts.suppress_summary_metadata:
        messenger.info("Summarizing node ages and lengths ...")
        for stree in tt_trees:
            tsum.annotate_nodes_and_edges(tree=stree, split_distribution=master_split_distribution)

    if opts.edge_summarization is None:
        if target_tree_filepath is not None:
            opts.edge_summarization = 'keep'
        else:
            if opts.ultrametric_trees:
                opts.edge_summarization = 'median-age'
            else:
                opts.edge_summarization = 'mean-length'
    if opts.edge_summarization is not None and opts.edge_summarization == 'unweighted':
        for stree in tt_trees:
            for edge in stree.postorder_edge_iter():
                edge.length = None
    elif opts.edge_summarization is not None and opts.edge_summarization != 'keep':
        if opts.edge_summarization.startswith('mean'):
            summary_func_desc = "mean"
            summarization_func = lambda x: statistics.mean_and_sample_variance(x)[0]
        else:
            summary_func_desc = "median"
            summarization_func = statistics.median
        if opts.edge_summarization.endswith("age"):
            messenger.info("Mapping node ages ...")
            comments.append("Setting node ages of output tree(s) to %s ages of corresponding nodes of input trees." % summary_func_desc)
            if opts.collapse_negative_edges:
                comments.append("Parent node ages coerced to be at least as old as oldest daughter node age.")
                collapse_negative_edges = True
                allow_negative_edges = False
            else:
                comments.append("Parent node ages not adjusted: negative edge lengths allowed.")
                collapse_negative_edges = False
                allow_negative_edges = True
            for stree in tt_trees:
                tsum.summarize_node_ages_on_tree(tree=stree,
                        split_distribution=master_split_distribution,
                        set_edge_lengths=True,
                        collapse_negative_edges=collapse_negative_edges,
                        allow_negative_edges=allow_negative_edges,
                        summarization_func=summarization_func)
        elif opts.edge_summarization.endswith("length"):
            messenger.info("Mapping edge lengths ...")
            comments.append("Setting edge lengths of output tree(s) to %s length of corresponding edges of input trees." % summary_func_desc)
            for stree in tt_trees:
                tsum.summarize_edge_lengths_on_tree(tree=stree,
                        split_distribution=master_split_distribution,
                        summarization_func=summarization_func)
    else:
        comments.append("Not setting edge lengths on output tree(s).")

    end_time = datetime.datetime.now()

    ###################################################
    #  RESULTS

    messenger.info("Writing results ...")

    final_run_report = []
    final_run_report.append("Began at: %s." % (start_time.isoformat(' ')))
    final_run_report.append("Ended at: %s." % (end_time.isoformat(' ')))
    hours, mins, secs = str(end_time-start_time).split(":")
    run_time = "Run time: %s hour(s), %s minute(s), %s second(s)." % (hours, mins, secs)
    final_run_report.append(run_time)

    result_trees = dendropy.TreeList(tt_trees, taxon_namespace=master_taxon_namespace)
    if opts.to_newick_format:
        result_trees.write_to_stream(output_dest,
                "newick",
                suppress_rooting=False,
                suppress_edge_lengths=False,
                unquoted_underscores=False,
                preserve_spaces=False,
                store_tree_weights=False,
                suppress_annotations=False,
                annotations_as_nhx=False,
                suppress_item_comments=False,
                suppress_leaf_taxon_labels=False,
                suppress_leaf_node_labels=True,
                suppress_internal_taxon_labels=False,
                suppress_internal_node_labels=False,
                node_label_element_separator=' ',
                node_label_compose_func=None,
                real_value_format_specifier=real_value_format_specifier,
                )
    else:
        if opts.include_taxa_block:
            simple = False
        else:
            simple = True
        if opts.include_meta_comments:
            comment = []
            try:
                username = getpass.getuser()
            except:
                username = "a user"
            comment.append("%s %s by %s." % (_program_name, _program_version, _program_author))
            comment.append("Using DendroPy Version %s by Jeet Sukumaran and Mark T. Holder."
                % dendropy.__version__)
            python_version = sys.version.replace("\n", "").replace("[", "(").replace("]",")")
            comment.append("Running under Python %s on %s." % (python_version, sys.platform))
            comment.append("Executed on %s by %s@%s." % (platform.node(),  username, socket.gethostname()))
            if support_filepaths is not None and len(support_filepaths) > 0:
                comment.append("Basis of split support:")
                for support_file in support_filepaths:
                    comment.append("  - '%s'" % os.path.abspath(support_file))
            else:
                comment.append("Basis of split support: trees read from standard input.")
            comment.extend(final_run_report)
            comment.extend(comments)
        if opts.additional_comments:
            comment.append("\n")
            comment.append(opts.additional_comments)
        result_trees.write_to_stream(output_dest,
                "nexus",
                simple=simple,
                file_comments=comment,
                suppress_rooting=False,
                suppress_edge_lengths=opts.edge_summarization == 'unweighted',
                unquoted_underscores=False,
                preserve_spaces=False,
                store_tree_weights=False,
                suppress_annotations=False,
                annotations_as_nhx=False,
                suppress_item_comments=False,
                suppress_leaf_taxon_labels=False,
                suppress_leaf_node_labels=True,
                suppress_internal_taxon_labels=False,
                suppress_internal_node_labels=False,
                node_label_element_separator=' ',
                node_label_compose_func=None,
                real_value_format_specifier=real_value_format_specifier,
                )

    if trprobs_dest:
        messenger.info("Writing tree probabilities ...")
        tree_list = dendropy.TreeList(taxon_namespace=master_split_distribution.taxon_namespace)
        tree_freqs = master_topology_counter.calc_tree_freqs(tree_list.taxon_namespace)
        cumulative_prob = 0.0
        for idx, (tree, (count, prop)) in enumerate(tree_freqs.items()):
            tree_list.append(tree)
            cumulative_prob += prop
            tree.probability = prop
            tree.count = count
            tree.cumulative_probability = cumulative_prob
            tree.annotations.add_bound_attribute('count')
            tree.annotations.add_bound_attribute('probability')
            tree.annotations.add_bound_attribute('cumulative_probability')
            tree.label = "Tree%d" % (idx+1)
        tree_list.write_to_stream(trprobs_dest,
                'nexus',
                simple=simple,
                suppress_rooting=False,
                suppress_edge_lengths=True,
                unquoted_underscores=False,
                preserve_spaces=False,
                store_tree_weights=False,
                suppress_annotations=False,
                annotations_as_nhx=False,
                suppress_item_comments=True,
                suppress_leaf_taxon_labels=False,
                suppress_leaf_node_labels=True,
                suppress_internal_taxon_labels=False,
                suppress_internal_node_labels=False,
                node_label_element_separator=' ',
                node_label_compose_func=None,
                real_value_format_specifier=real_value_format_specifier,
                )

    if split_edge_map_dest:
        messenger.info("Writing split edge lengths ...")
        for split in master_split_distribution.splits:
            row = []
            row.append(master_split_distribution.taxon_namespace.split_as_newick_string(split))
            for edge_length in master_split_distribution.split_edge_lengths[split]:
                row.append("%s" % edge_length)
            split_edge_map_dest.write("%s\n" % ("\t".join(row)))

    if not opts.output_filepath:
        pass
    else:
        messenger.info("Results written to: '%s'." % (output_fpath))

    ###################################################
    #  WRAP UP
    messenger.info("Summarization completed.")
    messenger.info_lines(final_run_report)
    messenger.silent = True

if __name__ == '__main__':
    main_cli()