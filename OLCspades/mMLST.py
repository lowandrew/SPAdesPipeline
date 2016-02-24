#!/usr/bin/env python
import operator
import os
import re
import shlex
import subprocess
import time
import json
from Queue import Queue
from collections import defaultdict
from glob import glob
from threading import Thread
from Bio import SeqIO
from Bio.Blast.Applications import NcbiblastnCommandline
from csv import DictReader
import getmlst
# Import accessory functions
from accessoryFunctions import dotter, make_path, make_dict, printtime, globalcounter

__author__ = 'akoziol, mikeknowles'
""" Includes threading found in examples:
http://www.troyfawkes.com/learn-python-multithreading-queues-basics/
http://www.ibm.com/developerworks/aix/library/au-threadingpython/
https://docs.python.org/2/library/threading.html
Revised with speed improvements
"""

# TODO keep CFIA profiles and alleles in separate files


class MLST(object):
    def mlst(self):
        # Get the MLST profiles into a dictionary for each sample
        printtime('Populating {} sequence profiles'.format(self.analysistype), self.start)
        self.profiler()
        globalcounter()
        # Make blast databases (if necessary)
        printtime('Creating {} blast databases as required'.format(self.analysistype), self.start)
        self.makedbthreads(self.allelefolders)
        # Run the blast analyses
        printtime('Running {} blast analyses'.format(self.analysistype), self.start)
        self.blastnthreads()
        globalcounter()
        # Determine sequence types from the analyses
        printtime('Determining {} sequence types'.format(self.analysistype), self.start)
        self.sequencetyper()
        globalcounter()
        # Create reports
        printtime('Creating {} reports'.format(self.analysistype), self.start)
        self.reporter()
        globalcounter()
        # Optionally dump :self.resultprofile to :self.reportpath
        if self.datadump:
            self.dumper()
            printtime('{} reference profile dump complete'.format(self.analysistype), self.start)
        # Optionally determine the closest reference genome from a pre-computed profile (this profile would have been
        # created using self.datadump
        # if self.bestreferencegenome and self.analysistype == 'rMLST':
        #     self.referencegenomefinder()
        printtime('{} analyses complete'.format(self.analysistype), self.start)

    def profiler(self):
        """Creates a dictionary from the profile scheme(s)"""
        # Initialise the variables
        profiledata = defaultdict(make_dict)
        profileset = set()
        genedict = {}
        # Find all the unique profiles to use with a set
        for sample in self.metadata:
            if sample.mlst.profile[self.analysistype] != 'NA':
                profileset.add(sample.mlst.profile[self.analysistype][0])
        # Extract the profiles for each set
        for sequenceprofile in profileset:
            # Clear the list of genes
            genelist = []
            for sample in self.metadata:
                genelist = [os.path.split(x)[1].split('.')[0] for x in sample.mlst.alleles[self.analysistype]]
            try:
                # Open the sequence profile file as a dictionary
                profile = DictReader(open(sequenceprofile), dialect='excel-tab')
                # Iterate through the rows
                for row in profile:
                    # Iterate through the genes
                    for gene in genelist:
                        # Add the sequence profile, and type, the gene name and the allele number to the dictionary
                        try:
                            profiledata[sequenceprofile][row['ST']][gene] = row[gene]
                        except KeyError:
                            profiledata[sequenceprofile][row['rST']][gene] = row[gene]
            # Revert to standard comma separated values
            except KeyError:
                # Open the sequence profile file as a dictionary
                profile = DictReader(open(sequenceprofile))
                # Iterate through the rows
                for row in profile:
                    # Iterate through the genes
                    for gene in genelist:
                        # Add the sequence profile, and type, the gene name and the allele number to the dictionary
                        try:
                            profiledata[sequenceprofile][row['ST']][gene] = row[gene]
                        except KeyError:
                            profiledata[sequenceprofile][row['rST']][gene] = row[gene]
            # Add the gene list to a dictionary
            genedict[sequenceprofile] = sorted(genelist)
        # Add the profile data, and gene list to each sample
        for sample in self.metadata:
            if sample.mlst.profile[self.analysistype] != 'NA':
                # Populate the metadata with the profile data
                sample.mlst.profiledata = {self.analysistype: profiledata[sample.mlst.profile[self.analysistype][0]]}
                # Add the allele directory to a list of directories used in this analysis
                self.allelefolders.add(sample.mlst.alleledir[self.analysistype])
                # Add the list of genes in the analysis to each sample
                sample.mlst.genelist = {self.analysistype: genedict[sample.mlst.profile[self.analysistype][0]]}
                dotter()
            # Add 'NA' values to samples lacking sample.mlst.profile[self.analysistype]
            else:
                sample.mlst.profiledata = {self.analysistype: 'NA'}
                sample.mlst.genelist = {self.analysistype: 'NA'}

    def makedbthreads(self, folder):
        """
        Setup and create threads for class
        :param folder: folder with sequence files with which to create blast databases
        """
        # Create and start threads for each fasta file in the list
        for i in range(len(folder)):
            # Send the threads to makeblastdb
            threads = Thread(target=self.makeblastdb, args=())
            # Set the daemon to true - something to do with thread management
            threads.setDaemon(True)
            # Start the threading
            threads.start()
        # Make blast databases for MLST files (if necessary)
        for alleledir in folder:
            # List comprehension to remove any previously created database files from list
            allelefiles = glob('{}/*.fasta'.format(alleledir))
            # For each allele file
            for allelefile in allelefiles:
                # Add the fasta file to the queue
                self.dqueue.put(allelefile)
        self.dqueue.join()  # wait on the dqueue until everything has been processed

    def makeblastdb(self):
        """Makes blast database files from targets as necessary"""
        while True:  # while daemon
            fastapath = self.dqueue.get()  # grabs fastapath from dqueue
            # remove the path and the file extension for easier future globbing
            db = fastapath.split('.')[0]
            nhr = '{}.nhr'.format(db)  # add nhr for searching
            fnull = open(os.devnull, 'w')  # define /dev/null
            if not os.path.isfile(str(nhr)):  # if check for already existing dbs
                # Create the databases
                subprocess.call(shlex.split('makeblastdb -in {} -parse_seqids -max_file_sz 2GB -dbtype nucl -out {}'
                                            .format(fastapath, db)), stdout=fnull, stderr=fnull)
            dotter()
            self.dqueue.task_done()  # signals to dqueue job is done

    def blastnthreads(self):
        """Setup and create  threads for blastn and xml path"""
        import threading
        # Create the threads for the BLAST analysis
        for sample in self.metadata:
            if sample.general.bestassemblyfile != 'NA':
                for i in range(len(sample.mlst.combinedalleles[self.analysistype])):
                    # On very large analyses, the thread count can exceed its maximum allowed value (probable 2^15 -
                    # 32768). This limits the number of threads to 1000
                    if threading.active_count() < 100:
                        threads = Thread(target=self.runblast, args=())
                        threads.setDaemon(True)
                        threads.start()
        # Populate threads for each gene, genome combination
        for sample in self.metadata:
            if sample.general.bestassemblyfile != 'NA':
                for allele in sample.mlst.combinedalleles[self.analysistype]:
                    # Add each fasta/allele file combination to the threads
                    self.blastqueue.put((sample.general.bestassemblyfile, allele, sample))
        # Join the threads
        self.blastqueue.join()

    def runblast(self):
        while True:  # while daemon
            (assembly, allele, sample) = self.blastqueue.get()  # grabs fastapath from dqueue
            genome = os.path.split(assembly)[1].split('.')[0]
            # Run the BioPython BLASTn module with the genome as query, fasta(target gene) as db,
            # re-perform the BLAST search each time
            make_path(self.reportpath)
            report = '{}{}_rawresults_{:}.csv'.format(self.reportpath, genome, time.strftime("%Y.%m.%d.%H.%M.%S"))
            db = allele.split('.')[0]
            # BLAST command line call. Note the mildly restrictive evalue, and the high number of alignments.
            # Due to the fact that all the targets are combined into one database, this is to ensure that all potential
            # alignments are reported. Also note the custom outfmt: the doubled quotes are necessary to get it work
            blastn = NcbiblastnCommandline(query=assembly, db=db, evalue='1E-20', num_alignments=1000000,
                                           num_threads=12,
                                           outfmt='"6 qseqid sseqid positive mismatch gaps '
                                                  'evalue bitscore slen length"',
                                           out=report)
            # Note that there is no output file specified -  the search results are currently stored in stdout
            blastn()
            # Run the blast parsing module
            self.blastparser(report, sample)
            self.blastqueue.task_done()  # signals to dqueue job is done

    def blastparser(self, report, sample):
        # Open the sequence profile file as a dictionary
        blastdict = DictReader(open(report), fieldnames=self.fieldnames, dialect='excel-tab')
        # Go through each BLAST result
        for row in blastdict:
            # Calculate the percent identity and extract the bitscore from the row
            # Percent identity is the (length of the alignment - number of mismatches) / total subject length
            percentidentity = (float(row['positives']) - float(row['gaps'])) / float(row['subject_length']) * 100
            bitscore = float(row['bit_score'])
            # Find the allele number and the text before the number for different formats
            allelenumber, gene = allelesplitter(row['subject_id'])
            # If the percent identity is 100, and there are no mismatches, the allele is a perfect match
            if percentidentity == 100 and float(row['mismatches']) == 0:
                # If there are multiple best hits, then the .values() will be populated
                if self.plusdict[sample.name][gene].values():
                    # If the previous best hit have under 100% identity, or if the current bitscore is better
                    if self.plusdict[sample.name][gene].values()[0].keys()[0] < 100:
                        # Clear the previous match
                        self.plusdict[sample.name][gene].clear()
                        # Populate the new match
                        self.plusdict[sample.name][gene][allelenumber][percentidentity] = bitscore
                    # If the bitscore is better (longer match) clear the previous result
                    # (not for rMLST analyses, which are allowed multiple allele matches)
                    else:
                        if bitscore > self.plusdict[sample.name][gene].values()[0].values() and \
                                self.analysistype != 'rMLST':
                            # Clear the previous match
                            self.plusdict[sample.name][gene].clear()
                            # Populate the new match
                            self.plusdict[sample.name][gene][allelenumber][percentidentity] = bitscore
                        else:
                            # Add the allele to the gene match
                            self.plusdict[sample.name][gene][allelenumber][percentidentity] = bitscore

                # Populate the match
                else:
                    self.plusdict[sample.name][gene][allelenumber][percentidentity] = bitscore
            # If the match is above the cutoff, but below 100%, add it to the dictionary
            elif percentidentity > self.cutoff:
                # If there are multiple best hits, then the .values() will be populated
                if self.plusdict[sample.name][gene].values():
                    if bitscore > self.plusdict[sample.name][gene].values()[0].values() and \
                            self.plusdict[sample.name][gene].values()[0].keys()[0] < 100:
                        # elif percentidentity > self.cutoff and gene not in self.plusdict[sample.name]:
                        self.plusdict[sample.name][gene][allelenumber][percentidentity] = bitscore
                else:
                    self.plusdict[sample.name][gene][allelenumber][percentidentity] = bitscore
        # Populate empty results for genes without any matches
        for gene in sample.mlst.allelenames[self.analysistype]:
            if gene not in self.plusdict[sample.name]:
                self.plusdict[sample.name][gene]['N'][0] = 0

    def sequencetyper(self):
        """Determines the sequence type of each strain based on comparisons to sequence type profiles"""
        # Initialise variables
        header = 0
        # Iterate through the genomes
        for sample in self.metadata:
            genome = sample.name
            # Initialise self.bestmatch[genome] with an int that will eventually be replaced by the number of matches
            self.bestmatch[genome] = defaultdict(int)
            if sample.mlst.profile[self.analysistype] != 'NA':
                # Create the profiledata variable to avoid writing sample.mlst.profiledata[self.analysistype]
                profiledata = sample.mlst.profiledata[self.analysistype]
                # For each gene in plusdict[genome]
                for gene in sample.mlst.genelist[self.analysistype]:
                    # Clear the appropriate count and lists
                    multiallele = []
                    multipercent = []
                    # Go through the alleles in plusdict
                    for allele in self.plusdict[genome][gene]:
                        percentid = self.plusdict[genome][gene][allele].keys()[0]
                        # "N" alleles screw up the allele splitter function
                        if allele != "N":
                            # Use the alleleSplitter function to get the allele number
                            # allelenumber, alleleprenumber = allelesplitter(allele)
                            # Append as appropriate - alleleNumber is treated as an integer for proper sorting
                            multiallele.append(int(allele))
                            multipercent.append(percentid)
                        # If the allele is "N"
                        else:
                            # Append "N" and a percent identity of 0
                            multiallele.append("N")
                            multipercent.append(0)
                        # Trying to catch cases that where the allele isn't "N", but can't be parsed by alleleSplitter
                        if not multiallele:
                            multiallele.append("N")
                            multipercent.append(0)
                    # Populate self.bestdict with genome, gene, alleles - joined with a space (this was written like
                    # this because allele is a list generated by the .iteritems() above, and the percent identity
                    self.bestdict[genome][gene][" ".join(str(allele)
                                                         for allele in sorted(multiallele))] = multipercent[0]
                    # Find the profile with the most alleles in common with the query genome
                    for sequencetype in profiledata:
                        # The number of genes in the analysis
                        header = len(profiledata[sequencetype])
                        # refallele is the allele number of the sequence type
                        refallele = profiledata[sequencetype][gene]
                        # If there are multiple allele matches for a gene in the reference profile e.g. 10 692
                        if len(refallele.split(" ")) > 1:
                            # Map the split (on a space) alleles as integers - if they are treated as integers,
                            # the alleles will sort properly
                            intrefallele = map(int, refallele.split(" "))
                            # Create a string of the joined, sorted alleles
                            sortedrefallele = " ".join(str(allele) for allele in sorted(intrefallele))
                        else:
                            # Use the reference allele as the sortedRefAllele
                            sortedrefallele = refallele
                        for allele, percentid in self.bestdict[genome][gene].iteritems():
                            # If the allele in the query genome matches the allele in the reference profile, add
                            # the result to the bestmatch dictionary. Because genes with multiple alleles were sorted
                            # the same way, these strings with multiple alleles will match: 10 692 will never be 692 10
                            if allele == sortedrefallele:
                                # Increment the number of matches to each profile
                                self.bestmatch[genome][sequencetype] += 1
                # Get the best number of matches
                # From: https://stackoverflow.com/questions/613183/sort-a-python-dictionary-by-value
                try:
                    sortedmatches = sorted(self.bestmatch[genome].items(), key=operator.itemgetter(1), reverse=True)[0][
                        1]
                # If there are no matches, set :sortedmatches to zero
                except IndexError:
                    sortedmatches = 0
                # If there are fewer matches than the total number of genes in the typing scheme
                if 0 < int(sortedmatches) < header:
                    # Iterate through the sequence types and the number of matches in bestDict for each genome
                    for sequencetype, matches in self.bestmatch[genome].iteritems():
                        # If the number of matches for a profile matches the best number of matches
                        if matches == sortedmatches:
                            # Iterate through the gene in the analysis
                            for gene in profiledata[sequencetype]:
                                # Get the reference allele as above
                                refallele = profiledata[sequencetype][gene]
                                # As above get the reference allele split and ordered as necessary
                                if len(refallele.split(" ")) > 1:
                                    intrefallele = map(int, refallele.split(" "))
                                    sortedrefallele = " ".join(str(allele) for allele in sorted(intrefallele))
                                else:
                                    sortedrefallele = refallele
                                # Populate :self.mlstseqtype with the genome, best match to profile, number of matches
                                # to the profile, gene, query allele(s), reference allele(s), and percent identity
                                if self.updateprofile:
                                    self.mlstseqtype[genome][sequencetype][sortedmatches][gene][
                                        str(self.bestdict[genome][gene]
                                            .keys()[0])][sortedrefallele] = str(self.bestdict[genome][gene].values()[0])
                                else:
                                    self.resultprofile[genome][sequencetype][sortedmatches][gene][
                                        self.bestdict[genome][gene]
                                            .keys()[0]] = str(self.bestdict[genome][gene].values()[0])
                    # Add the new profile to the profile file (if the option is enabled)
                    if self.updateprofile:
                        self.reprofiler(int(header), sample.mlst.profile[self.analysistype][0], genome)
                elif sortedmatches == 0:
                    for gene in sample.mlst.genelist[self.analysistype]:
                        # Populate the profile of results with 'negative' values for sequence type and sorted matches
                        self.resultprofile[genome]['0'][sortedmatches][gene][self.bestdict[genome][gene]
                                                                             .keys()[0]] = str(self.bestdict[genome]
                                                                                               [gene].values()[0])
                    # Add the new profile to the profile file (if the option is enabled)
                    if self.updateprofile:
                        self.reprofiler(int(header), sample.mlst.profile[self.analysistype][0], genome)
                # Otherwise, the query profile matches the reference profile
                else:
                    # Iterate through best match
                    for sequencetype, matches in self.bestmatch[genome].iteritems():
                        if matches == sortedmatches:
                            for gene in profiledata[sequencetype]:
                                # Populate resultProfile with the genome, best match to profile, number of matches
                                # to the profile, gene, query allele(s), reference allele(s), and percent identity
                                self.resultprofile[genome][sequencetype][sortedmatches][gene][
                                    self.bestdict[genome][gene]
                                        .keys()[0]] = str(self.bestdict[genome][gene].values()[0])
                dotter()

    def reprofiler(self, header, profilefile, genome):
        # reprofiler(numGenes, profileFile, geneList, genome)
        """
        Creates and appends new profiles as required
        :param header:
        :param profilefile:
        :param genome:
        """
        # Iterate through mlstseqtype - it contains genomes with partial matches to current reference profiles
        # Reset :newprofile
        newprofile = ""
        # Find the last profile entry in the dictionary of profiles
        # Opens uses the command line tool 'tail' to look at the last line of the file (-1). This last line
        # is split on tabs, and only the first entry (the sequence type number) is captured
        profile = subprocess.check_output(['tail', '-1', profilefile]).split("\t")[0]
        # Split the _CFIA from the number - if there is no "_", the just use profile as the profile number
        try:
            profilenumber = int(profile.split("_")[0])
        except IndexError:
            profilenumber = int(profile)
        # If the number is less than 1000000, then new profiles have not previously been added
        if profilenumber < 1000000:
            # Set the new last entry number to be 1000000
            lastentry = 1000000
        # If profiles have previously been added
        else:
            # Set last entry to the highest profile number plus one
            lastentry = profilenumber + 1
        # As there can be multiple profiles in MLSTSeqType, this loop only needs to be performed once.
        seqcount = 0
        # Go through the sequence types
        try:
            sequencetype = self.mlstseqtype[genome].keys()[0]
        except IndexError:
            sequencetype = ''
            seqcount = 1
        # Only do this once
        if seqcount == 0:
            # Set the :newprofile string to start with the new profile name (e.g. 1000000_CFIA)
            newprofile = '{}_CFIA'.format(str(lastentry))
            # The number of matches to the reference profile
            nummatches = self.mlstseqtype[genome][sequencetype].keys()[0]
            for sample in self.metadata:
                if sample.name == genome:
                    # The genes in geneList - should be in the correct order
                    for gene in sample.mlst.genelist[self.analysistype]:
                        # The allele for each gene in the query genome
                        allele = self.mlstseqtype[genome][sequencetype][nummatches][gene].keys()[0]
                        # Append the allele to newprofile
                        newprofile += '\t{}'.format(allele)
                        # Add the MLST results for the query genome as well as the new profile data
                        # to resultProfile
                        self.resultprofile[genome]['{}_CFIA'.format(str(lastentry))][header][gene][allele] = \
                            self.mlstseqtype[genome][sequencetype][nummatches][gene][allele].values()[0]
                    seqcount += 1
        # Only perform the next loop if :newprofile exists
        if newprofile:
            # Open the profile file to append
            with open(profilefile, "ab") as appendfile:
                # Append the new profile to the end of the profile file
                appendfile.write("%s\n" % newprofile)
            # Re-run profiler with the updated files
            self.profiler()

    def reporter(self):
        """ Parse the results into a report"""
        # Initialise variables
        row = ''
        reportdirset = set()
        # Populate a set of all the report directories to use. A standard analysis will only have a single report
        # directory, while pipeline analyses will have as many report directories as there are assembled samples
        for sample in self.metadata:
            # Ignore samples that lack a populated reportdir attribute
            if sample.mlst.reportdir[self.analysistype] != 'NA':
                make_path(sample.mlst.reportdir[self.analysistype])
                # Add to the set - I probably could have used a counter here, but I decided against it
                reportdirset.add(sample.mlst.reportdir[self.analysistype])
        # Create a report for each sample from :self.resultprofile
        for sample in self.metadata:
            if sample.mlst.reportdir[self.analysistype] != 'NA':
                # Populate the header with the appropriate data, including all the genes in the list of targets
                row += 'Strain,SequenceType,Matches,{},\n'.format(','.join(sample.mlst.genelist[self.analysistype]))
                # Set the sequence counter to 0. This will be used when a sample has multiple best sequence types.
                # The name of the sample will not be written on subsequent rows in order to make the report clearer
                seqcount = 0
                # Iterate through the best sequence types for the sample (only occurs if update profile is disabled)
                for seqtype in self.resultprofile[sample.name]:
                    """
                    {
                        "OLF15230-1_2015-SEQ-0783": {
                            "1000004_CFIA": {
                                "7": {
                                    "dnaE": {
                                        "47": "100.00"
                                    },
                                    "dtdS": {
                                        "19": "100.00"
                                    },
                                    "gyrB": {
                                        "359": "100.00"
                                    },
                                    "pntA": {
                                        "50": "100.00"
                                    },
                                    "pyrC": {
                                        "143": "100.00"
                                    },
                                    "recA": {
                                        "31": "100.00"
                                    },
                                    "tnaA": {
                                        "26": "100.00"
                                    }
                                }
                            }
                        }
                    }
                    """
                    # Becomes
                    """
                    Strain,SequenceType,Matches,dnaE,gyrB,recA,dtdS,pntA,pyrC,tnaA
                    OLF15230-1_2015-SEQ-0783,1000004_CFIA,7,26 (100.00%),359 (100.00%),31 (100.00%),50 (100.00%),
                        19 (100.00%),47 (100.00%),143 (100.00%)
                    """
                    # The number of matches to the profile
                    matches = self.resultprofile[sample.name][seqtype].keys()[0]
                    # If this is the first of one or more sequence types, include the sample name
                    if seqcount == 0:
                        row += '{},{},{},'.format(sample.name, seqtype, matches)
                    # Otherwise, skip the sample name
                    else:
                        row += ',{},{},'.format(seqtype, matches)
                    # Iterate through all the genes present in the analyses for the sample
                    for gene in sample.mlst.genelist[self.analysistype]:
                        refallele = sample.mlst.profiledata[self.analysistype][seqtype][gene]
                        # Set the allele and percent id from the dictionary's keys and values, respectively
                        allele = self.resultprofile[sample.name][seqtype][matches][gene].keys()[0]
                        percentid = self.resultprofile[sample.name][seqtype][matches][gene].values()[0]
                        if refallele != allele:
                            if 0 < percentid < 100:
                                row += '{} ({}%),'.format(allele, percentid)
                            else:
                                row += '{} ({}),'.format(allele, refallele)
                        else:
                            # Add the allele and percent id to the row (only add the percent identity if it is not 100%)
                            if 0 < percentid < 100:
                                row += '{} ({}%),'.format(allele, percentid)
                            else:
                                row += '{},'.format(allele)
                        self.referenceprofile[sample.name][gene] = allele
                        sample.mlst.referenceprofile = {self.analysistype: {gene: allele}}
                    # Add a newline
                    row += '\n'
                    # Increment the number of sequence types observed for the sample
                    seqcount += 1
                # If the length of the number of report directories is greater than 1 (script is being run as part of
                # the assembly pipeline) make a report for each sample
                if len(reportdirset) > 1:
                    # Open the report
                    with open('{}{}_{}.csv'.format(sample.mlst.reportdir[self.analysistype], sample.name,
                                                   self.analysistype), 'wb') as report:
                        # Write the row to the report
                        report.write(row)
            dotter()
        # Create the report folder
        make_path(self.reportpath)
        # Create the report containing all the data from all samples
        with open('{}{}_{:}.csv'.format(self.reportpath, self.analysistype, time.strftime("%Y.%m.%d.%H.%M.%S")), 'wb') \
                as combinedreport:
            # Write the results to this report
            combinedreport.write(row)
        # Remove the raw results csv
        [os.remove(rawresults) for rawresults in glob('{}*rawresults*'.format(self.reportpath))]

    def dumper(self):
        """Write :self.referenceprofile"""
        with open('{}{}_referenceprofile.json'.format(self.reportpath, self.analysistype,), 'wb') as referenceprofile:
            referenceprofile.write(json.dumps(self.referenceprofile, sort_keys=True, indent=4, separators=(',', ': ')))

    # def referencegenomefinder(self):
    #     referencematch = defaultdict(make_dict)
        # referencehits = defaultdict(make_dict)
        # referencegenomeprofile = '{}/rMLST_referenceprofile.json'\
        #     .format([x for x in self.allelefolders if 'rMLST' in x][0])
        # with open(referencegenomeprofile) as referencefile:
        #     referencetypes = json.load(referencefile)
        # for sample in self.metadata:
        #     if sample.mlst.reportdir[self.analysistype] != 'NA':
        #         for genome in referencetypes:
        #             referencehits[sample.name][genome] = 0
        #             for gene in self.bestdict[sample.name]:
        #                 if self.bestdict[sample.name][gene].keys()[0] == referencetypes[genome][gene]:
        #                     referencematch[sample.name][genome][gene] = 1
        #                     referencehits[sample.name][genome] += 1
        #                 else:
        #                     referencematch[sample.name][genome][gene] = 0
        #
        # for sample in self.metadata:
        #     if sample.mlst.reportdir[self.analysistype] != 'NA':
        #     # Get the best number of matches
        #         # From: https://stackoverflow.com/questions/613183/sort-a-python-dictionary-by-value
        #         # [1]
        #         try:
        #             sortedmatches = sorted(referencehits[sample.name].items(),
        # key=operator.itemgetter(1), reverse=True)[0]
        #         except IndexError:
        #             sortedmatches = (0, 0)

        # print json.dumps(self.resultprofile, sort_keys=True, indent=4, separators=(',', ': '))
        #

    def __init__(self, inputobject):
        self.path = inputobject.path
        self.metadata = inputobject.runmetadata.samples
        self.cutoff = inputobject.cutoff
        self.start = inputobject.start
        self.analysistype = inputobject.analysistype
        self.allelefolders = set()
        self.updateallele = inputobject.updateallele
        self.updateprofile = inputobject.updateprofile
        self.updatedb = []
        self.reportpath = inputobject.reportdir
        self.datadump = inputobject.datadump
        self.bestreferencegenome = inputobject.bestreferencegenome
        # Fields used for custom outfmt 6 BLAST output:
        # "6 qseqid sseqid positive mismatch gaps evalue bitscore slen length"
        self.fieldnames = ['query_id', 'subject_id', 'positives', 'mismatches', 'gaps',
                           'evalue',  'bit_score', 'subject_length', 'alignment_length']
        # Declare queues, and dictionaries
        self.dqueue = Queue()
        self.blastqueue = Queue()
        self.blastdict = {}
        self.blastresults = defaultdict(make_dict)
        self.plusdict = defaultdict(make_dict)
        self.bestdict = defaultdict(make_dict)
        self.bestmatch = defaultdict(int)
        self.mlstseqtype = defaultdict(make_dict)
        self.resultprofile = defaultdict(make_dict)
        self.profiledata = defaultdict(make_dict)
        self.referenceprofile = defaultdict(make_dict)
        # Run the MLST analyses
        self.mlst()


def allelesplitter(allelenames):
    # Multiple try-excepts. Maybe overly complicated, but I couldn't get it work any other way
    # This (hopefully) accounts for all the possible naming schemes for the alleles
    try:  # no split - just allele numbers e.g. >12
        match = re.search(r"(>\d+)", allelenames)
        allelenumber = str(match.group()).split(">")[1]
        alleleprenumber = ""
    except (IndexError, AttributeError):
        try:  # split on "_" e.g. >AROC_12
            # allelenumber is the number of the allele(!). It should be different for each allele
            allelenumber = allelenames.split("_")[1]
            # alleleprenumber is anything before the allele number. It should be the same for each allele
            alleleprenumber = allelenames.split("_")[0]
        except IndexError:
            try:  # split on "-" e.g. >AROC-12
                allelenumber = allelenames.split("-")[1]
                alleleprenumber = allelenames.split("-")[0]
            except IndexError:
                try:  # split on " " e.g. >AROC 12
                    allelenumber = allelenames.split(" ")[1]
                    alleleprenumber = allelenames.split(" ")[0]
                except IndexError:
                    try:  # split on change from letters to numbers e.g. >AROC12
                        match = re.search(r"(>[A-z/a-z]+)(\d+)", allelenames)
                        allelenumber = match.groups()[1]
                        alleleprenumber = match.groups()[0]
                    except (IndexError, AttributeError):
                        allelenumber = allelenames
                        alleleprenumber = allelenames
    # Return the variables
    return int(allelenumber), alleleprenumber


def blastdatabaseclearer(genepath):
    """
    Due to the nature of the program updating allele files, it's not desirable to use previously generated databases.
    Additionally, with the use of these files by multiple programs, there is an issue. This script makes database files
    as follows: aroC.fasta becomes aroC.nhr, etc. The current SPAdes assembly pipeline would take that same .fasta file
    and create aroC.fasta.nhr. Deleting database files prevents issues with glob including database files.
    :param genepath: path to folder containing the MLST target genes
    """
    # Get all the .nhr, .nin, .nsq files
    databaselist = glob('{}/*.n*'.format(genepath))
    # And delete them
    for allele in databaselist:
        os.remove(allele)


if __name__ == '__main__':
    class Parser(object):

        def strainer(self):
            from accessoryFunctions import GenObject, MetadataObject
            # Get the sequences in the sequences folder into a list. Note that they must have a file extension that
            # begins with .fa
            self.strains = sorted(glob('{}*.fa*'.format(self.sequencepath))) if self.sequencepath \
                else sorted(glob('{}sequences/*.fa*'.format(self.path)))
            # Populate the metadata object. This object will be populated to mirror the objects created in the
            # genome assembly pipeline. This way this script will be able to be used as a stand-alone, or as part
            # of a pipeline
            for sample in self.strains:
                # Create the object
                metadata = MetadataObject()
                # Set the base file name of the sequence. Just remove the file extension
                filename = os.path.split(sample)[1].split('.')[0]
                # Set the .name attribute to be the file name
                metadata.name = filename
                # Create the .general attribute
                metadata.general = GenObject()
                # Create the .mlst attribute
                metadata.mlst = GenObject()
                # Set the .general.bestassembly file to be the name and path of the sequence file
                metadata.general.bestassemblyfile = sample
                # Append the metadata for each sample to the list of samples
                self.samples.append(metadata)

        def organismchooser(self):
            """Allows the user to choose which organism to be used in the analyses"""
            # Initialise a count variable to be used in extracting the desired entry from a list of organisms
            orgcount = 0
            schemecount = 0
            # If the path of the folder containing the allele and profile subfolders is provided
            if self.allelepath:
                # Remove and previously created blast database files
                blastdatabaseclearer(self.allelepath)
                # Create lists of the alleles, and the profile
                self.alleles = glob('{}/*.tfa'.format(self.allelepath))
                # Get the .txt profile file name and path into a variable
                self.profile = glob('{}/*.txt'.format(self.allelepath))
            else:
                # If the name of the organism to analyse was provided
                if not self.organism:
                    # Get a list of the organisms in the (default) Organism subfolder
                    if not self.organismpath:
                        organismlist = glob('{}organism/*'.format(self.path))
                    elif self.organismpath:
                        organismlist = glob('{}*'.format(self.organismpath))
                    else:
                        organismlist = []
                    # Iterate through the sorted list
                    for folder in sorted(organismlist):
                        # Ensure that folder is, in actuality, a folder
                        if os.path.isdir(folder):
                            # Print out the folder names and the count
                            print "[{}]: {}".format(orgcount, os.path.split(folder)[1])
                            orgcount += 1
                    # Get the user input - the number entered corresponds to the list index
                    response = input("Please select an organism: ")
                    # Get the organism path into a variable
                    organism = sorted(organismlist)[int(response)]
                    self.organism = os.path.split(organism)[1]
                    self.organismpath = self.organismpath if self.organismpath else '{}organism/{}' \
                        .format(self.path, self.organism)
                # If the name wasn't provided
                else:
                    # Set the organism path as the path + Organism + organism name
                    self.organismpath = '{}organism/{}'.format(self.path, self.organism)
                if not self.scheme:
                    schemelist = glob('{}/*'.format(self.organismpath))
                    # Iterate through the sorted list
                    for folder in sorted(schemelist):
                        # Ensure that folder is, in actuality, a folder
                        if os.path.isdir(folder):
                            # Print out the folder names and the count
                            print '[{}]: {}'.format(schemecount, os.path.split(folder)[1])
                            schemecount += 1
                    # Same as above
                    schemeresponse = input("Please select a typing scheme:")
                    self.allelepath = sorted(schemelist)[int(schemeresponse)]
                    # noinspection PyTypeChecker
                    self.scheme = os.path.split(self.allelepath)[1]
                    # Optionally get the newest profiles and alleles from pubmlst
                    if self.getmlst:
                        self.getmlsthelper()
                else:
                    # Otherwise set scheme as follows:
                    self.allelepath = '{}/{}'.format(self.organismpath, self.scheme)
                # Set the variables as above
                blastdatabaseclearer(self.allelepath)
                # Optionally get the newest profiles and alleles from pubmlst
                if self.getmlst and self.organism:
                    self.getmlsthelper()
                # Create lists of the alleles, and the profile
                self.alleles = glob('{}/*.tfa'.format(self.allelepath))
                # Set the name and path of the profile file
                self.profile = glob('{}/*.txt'.format(self.allelepath))
                self.combinedalleles = glob('{}/*.fasta'.format(self.allelepath))
                # If the combined alleles files doesn't exist
                size = 0
                if self.combinedalleles:
                    size = os.stat(self.combinedalleles[0]).st_size
                if not self.combinedalleles or size == 0:
                    # Open the combined allele file to write
                    with open('{}/{}_combined.fasta'.format(self.allelepath, self.scheme), 'wb') as combinedfile:
                        # Open each allele file
                        for allele in sorted(self.alleles):
                            # with open(allele, 'rU') as fasta:
                            for record in SeqIO.parse(open(allele, "rU"), "fasta"):
                                # Extract the sequence record from each entry in the multifasta
                                # Replace and dashes in the record.id with underscores
                                record.id = record.id.replace('-', '_')
                                # Remove and dashes or 'N's from the sequence data - makeblastdb can't handle sequences
                                # with gaps
                                record.seq._data = record.seq._data.replace('-', '').replace('N', '')
                                # Clear the name and description attributes of the record
                                record.name = ''
                                record.description = ''
                                # Write each record to the combined file
                                SeqIO.write(record, combinedfile, 'fasta')
                    # Set the combined alleles file name and path
                    self.combinedalleles = glob('{}/*.fasta'.format(self.allelepath))
            # Add the appropriate variables to the metadata object for each sample
            for sample in self.samples:
                sample.mlst.alleles = {self.scheme: self.alleles}
                sample.mlst.allelenames = {self.scheme: [os.path.split(x)[1].split('.')[0] for x in self.alleles]}
                sample.mlst.alleledir = {self.scheme: '{}/'.format(self.allelepath)}
                sample.mlst.profile = {self.scheme: self.profile}
                sample.mlst.analysistype = {self.scheme: self.scheme}
                sample.mlst.reportdir = {self.scheme: self.reportpath}
                sample.mlst.organism = {self.scheme: self.organism}
                sample.mlst.combinedalleles = {self.scheme: self.combinedalleles}

        def getmlsthelper(self):
            """Prepares to run the getmlst.py script provided in SRST2"""
            from accessoryFunctions import GenObject
            # Initialise a set to for the organism(s) for which new alleles and profiles are desired
            organismset = set()
            organismdictionary = {'Escherichia': 'Escherichia coli#1',
                                  'Vibrio': 'Vibrio parahaemolyticus',
                                  'Listeria': 'Listeria',
                                  'Campylobacter': 'Campylobacter jejuni',
                                  'Salmonella': 'Salmonella',
                                  'Staphylococcus': 'Staphylococcus'}
            # rMLST alleles cannot be fetched in the same way
            if self.scheme != 'rMLST':
                # Add the organism to the set
                organismset.add(organismdictionary[self.organism])
            for organism in organismset:
                # Create the object to store the argument attributes to feed to getmlst
                getmlstargs = GenObject()
                getmlstargs.species = organism
                getmlstargs.repository_url = 'http://pubmlst.org/data/dbases.xml'
                getmlstargs.force_scheme_name = False
                getmlstargs.path = '/home/blais/PycharmProjects/MLST/organism/{}/{}'.format(self.organism, self.scheme)
                # Create the path to store the downloaded
                make_path(getmlstargs.path)
                getmlst.main(getmlstargs)

        def __init__(self):
            from argparse import ArgumentParser
            parser = ArgumentParser(description='Performs blast analyses to determine presence of alleles in a genome '
                                                'query, and types genome based on typing profile. Adds novel alleles '
                                                'and profiles to the appropriate files. '
                                                'Example command: '
                                                '-p /home/blais/PycharmProjects/MLST  '
                                                '-s /home/blais/PycharmProjects/MLST/sequences '
                                                '-O /home/blais/PycharmProjects/MLST/Organism '
                                                '-o Vibrio '
                                                '-S MLST')
            parser.add_argument('-p', '--path', required=False, default=os.getcwd(),
                                help='Specify path for custom folder locations. If you don\'t supply additional paths'
                                     'e.g. sequencepath, allelepath, or organismpath, then the program will look for '
                                     'MLST files in .../path/Organism, and the query sequences in ../path/sequences. '
                                     'If you don\'t input a path, then the current working directory will be used.')
            parser.add_argument('-c', '--cutoff', required=False, default=98,
                                help='The percent identity cutoff value for BLAST matches. Default is 98%)')
            parser.add_argument('-s', '--sequencepath', required=False,
                                default='/home/blais/PycharmProjects/MLST/sequences',
                                help='The location of the query sequence files')
            parser.add_argument('-a', '--alleleprofilepath', required=False,
                                help='The path of the folder containing the two folders containing '
                                     'the allele files, and the profile file e.g. /folder/path/Organism/Vibrio/cgMLST'
                                     'Please note the requirements for the profile database in the readme')
            parser.add_argument('-O', '--organismpath', required=False,
                                help='The path of the folder containing the organism folders e.g. folder/path/Organism')
            parser.add_argument('-o', '--organism', required=False,
                                help='The name of the organism you wish to type. Must match the folder name containing '
                                     'the schemes e.g. Salmonella')
            parser.add_argument('-S', '--scheme', required=False,
                                help='The scheme you wish to use. Must match the folder name containing the scheme e.g.'
                                     ' cgMLST. Furthermore, this folder must contain two folders: "alleles" and '
                                     '"profile". The alleles folder contains the allele files in .fasta format, and the'
                                     ' profile folder contains the profile in .txt format. Please note the requirements'
                                     ' for the profile in the readme')
            parser.add_argument('-u', '--updateprofilefalse', required=False, action='store_false', default=True,
                                help='By default, the program automatically creates new sequence profiles and appends '
                                     'these profiles to the profile file. If, instead, you wish to wish to see the '
                                     'closest match of a query genome to known reference profiles, set this to False.')
            parser.add_argument('-U', '--updateallelefalse', required=False, action='store_false', default=True,
                                help='By default, the program automatically creates new alleles and appends these '
                                     'alleles to the appropriate file. If, instead, you wish to wish to see the '
                                     'closest match of a query genome to known reference alleles, set this to False.')
            parser.add_argument('-r', '--reportdirectory', default='{}/reports'.format(os.getcwd()),
                                help='Path to store the reports defaults to os.getcwd()/reports')
            parser.add_argument('-d', '--dumpdata', action='store_true', help='Optionally dump :self.resultprofile'
                                'dictionary to file. Useful when creating a reference database against which novel'
                                'sequences can be compared. The .json file will be placed in the reports folder ')
            parser.add_argument('-g', '--getmlst', action='store_true', help='Optionally get the newest profile'
                                'and alleles for your analysis from pubmlst.org')
            parser.add_argument('-b', '--bestreferencegenome', action='store_true', help='Optionally find the refseq '
                                'genome with the largest number of rMLST alleles in common with the strain of interest')

            # Get the arguments into an object
            args = parser.parse_args()
            # Define variables from the arguments - there may be a more streamlined way to do this
            # Add trailing slashes to the path variables to ensure consistent formatting (os.path.join)
            self.path = os.path.join(args.path, '')
            self.reportpath = os.path.join(args.reportdirectory, '')
            self.cutoff = float(args.cutoff)
            self.sequencepath = os.path.join(args.sequencepath, '') if args.sequencepath else ''
            self.allelepath = os.path.join(args.alleleprofilepath, '') if args.alleleprofilepath else ''
            self.organismpath = os.path.join(args.organismpath, '') if args.organismpath else ''
            self.scheme = args.scheme
            self.organism = args.organism
            self.updateprofile = args.updateprofilefalse
            self.updateallele = args.updateallelefalse
            self.datadump = args.dumpdata
            self.getmlst = args.getmlst
            self.bestreferencegenome = args.bestreferencegenome

            # Initialise variables
            self.genepath = ''
            self.alleles = ''
            self.combinedalleles = ''
            self.profile = ''
            self.strains = []
            self.samples = []
            # self.schemepath = ''
            # Get a list of the sequence files
            self.strainer()
            self.organismchooser()


    class MetadataInit(object):
        def __init__(self):
            # Run the parser
            self.runmetadata = Parser()
            # Get the appropriate variables from the metadata file
            self.path = self.runmetadata.path
            self.start = time.time()
            self.analysistype = self.runmetadata.scheme
            self.alleles = self.runmetadata.alleles
            self.profile = self.runmetadata.profile
            self.cutoff = self.runmetadata.cutoff
            self.updateallele = self.runmetadata.updateallele
            self.updateprofile = self.runmetadata.updateprofile
            self.reportdir = self.runmetadata.reportpath
            self.datadump = self.runmetadata.datadump
            self.getmlst = self.runmetadata.getmlst
            self.bestreferencegenome = self.runmetadata.bestreferencegenome
            # Run the analyses
            MLST(self)

    # Run the class
    MetadataInit()


class PipelineInit(object):
    def strainer(self):
        from accessoryFunctions import GenObject
        for sample in self.runmetadata.samples:
            if sample.general.bestassemblyfile != 'NA':
                sample.mlst = GenObject()
                if self.analysistype == 'rmlst':
                    self.alleles = glob('{}rMLST/*.tfa'.format(self.referencefilepath))
                    self.profile = glob('{}rMLST/*.txt'.format(self.referencefilepath))
                    self.combinedalleles = glob('{}rMLST/*.fasta'.format(self.referencefilepath))
                # Set the metadata file appropriately
                sample.mlst.alleles = {self.analysistype: self.alleles}
                sample.mlst.allelenames = {self.analysistype: [os.path.split(x)[1].split('.')[0] for x in self.alleles]}
                sample.mlst.alleledir = {self.analysistype: '{}rMLST/alleles/'.format(self.referencefilepath)}
                sample.mlst.profile = {self.analysistype: self.profile}
                sample.mlst.analysistype = {self.analysistype: self.analysistype}
                sample.mlst.reportdir = {self.analysistype: '{}/{}/'.format(sample.general.outputdirectory,
                                                                            self.analysistype)}
                sample.mlst.combinedalleles = {self.analysistype: self.combinedalleles}
            else:
                # Set the metadata file appropriately
                sample.mlst.alleles = {self.analysistype: 'NA'}
                sample.mlst.allelenames = {self.analysistype: 'NA'}
                sample.mlst.profile = {self.analysistype: 'NA'}
                sample.mlst.analysistype = {self.analysistype: 'NA'}
                sample.mlst.reportdir = {self.analysistype: 'NA'}
                sample.mlst.combinedalleles = {self.analysistype: 'NA'}

    def __init__(self, inputobject, analysistype):
        self.runmetadata = inputobject.runmetadata
        self.analysistype = analysistype
        self.path = inputobject.path
        self.start = inputobject.starttime
        self.referencefilepath = inputobject.reffilepath
        self.reportdir = '{}/'.format(inputobject.reportpath)
        self.alleles = ''
        self.profile = ''
        self.combinedalleles = ''
        self.cutoff = 100
        self.updateallele = True
        self.updateprofile = True
        self.datadump = False
        self.bestreferencegenome = True
        # Get the alleles and profile into the metadata
        self.strainer()
        MLST(self)
