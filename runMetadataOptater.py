__author__ = 'akoziol'

import re
import os
from collections import defaultdict
import json

# Import ElementTree - try first to import the faster C version, if that doesn't
# work, try to import the regular version
try:
    import xml.etree.cElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET

# Initialise variables
flowcell = ""
instrument = ""
reads = []
samples = []
IDs = []
elementData = []


def make_dict():
    """Makes Perl-style dictionaries"""
    return defaultdict(make_dict)

# Initialise the dictionary responsible for storing the report data
returnData = defaultdict(make_dict)


def parseRunInfo():
    """Parses the run information file (RunInfo.xml)"""
    global flowcell
    global instrument
    # Use elementTree to get the xml file into memory
    if os.path.isfile("RunInfo.xml"):
        runInfo = ET.ElementTree(file="RunInfo.xml")
        # pull the text from flowcell and instrument values using the .iter(tag="X") function
        for elem in runInfo.iter(tag="Flowcell"):
            flowcell = elem.text
        for elem in runInfo.iter(tag="Instrument"):
            instrument = elem.text
    else:
        flowcell = "N/A"
        instrument = "N/A"


def parseSampleSheet():
    """Parses the sample sheet (SampleSheet.csv) to determine certain values
    important for the creation of the assembly report"""
    global returnData
    global samples
    global IDs
    sampleSheet = open("SampleSheet.csv", "r")
    # Go line-by-line through the csv file to find the information required
    for line in sampleSheet:
        # Remove all newlines
        line.rstrip()
        # As this is a csv file, data are separated by commas - split on commas
        data = line.split(",")
        # Populate variables with appropriate data
        if re.search("Investigator", line):
            investigator = data[1].rstrip()
        elif re.search("Experiment", line):
            experiment = data[1].rstrip().replace("  ", " ")
        elif re.search("Date", line):
            date = data[1].rstrip().replace("/", "-")
        # Here's a Perl-like solution for reading lines after a regex match
        # Perform the regex
        elif re.search("Reads", line):
            # Now read all sublines going forward
            for subline in sampleSheet:
                # Stop reading once "Settings" is matched
                if re.search("Settings", subline):
                    break
                reads.append(subline)
            # Grab the number of reads in the first and second reads
            forwardLength = reads[0].rstrip().replace(",", "")
            reverseLength = reads[1].rstrip().replace(",", "")
        if re.search("Adapter", line):
            adapter = data[1].rstrip()
        elif re.search("Sample_ID", line):
            for subline in sampleSheet:
                subdata = subline.split(",")
                # Capture Sample_ID, Sample_Name, I7_Index_ID, index1, I5_Index_ID,	index2, Sample_Project
                sampleID = subdata[0]
                strain = subdata[1].rstrip().replace(" ", "-").replace(".", "-").replace("=", "-").replace("+", "").replace("/", "-").replace("#", "").replace("---", "-").replace("--", "-")
                returnData[strain]["3.Run"]["SampleName"] = subdata[0].rstrip()
                returnData[strain]["3.Run"]["I7IndexID"] = subdata[4].rstrip()
                returnData[strain]["3.Run"]["index1"] = subdata[5].rstrip()
                returnData[strain]["3.Run"]["I5IndexID"] = subdata[6].rstrip()
                returnData[strain]["3.Run"]["index2"] = subdata[7].rstrip()
                returnData[strain]["3.Run"]["Project"] = subdata[8].rstrip()
                returnData[strain]["3.Run"]["Investigator"] = investigator
                returnData[strain]["3.Run"]["Experiment"] = experiment
                returnData[strain]["3.Run"]["Date"] = date
                returnData[strain]["3.Run"]["AdapterSequence"] = adapter
                returnData[strain]["3.Run"]["LengthofFirstRead"] = forwardLength
                returnData[strain]["3.Run"]["LengthofSecondRead"] = reverseLength
                returnData[strain]["3.Run"]["Flowcell"] = flowcell
                returnData[strain]["3.Run"]["Instrument"] = instrument
                # Make a list of sample names to return to the main script
                IDs.append([sampleID, strain])
                samples.append(strain)
    return date, returnData, samples, int(forwardLength)


def parseRunStats(passedMetadata):
    """Parses the XML run statistics file (GenerateFASTQRunStatistics.xml)"""
    global totalClustersPF
    dataList = ["SampleNumber", "SampleID", "SampleName", "NumberOfClustersPF"]
    runStatistics = ET.ElementTree(file="GenerateFASTQRunStatistics.xml")
    # .iterfind() allow for the matching and iterating though matches
    for elem in runStatistics.iterfind("RunStats/NumberOfClustersPF"):
        # This is stored as a float to allow subsequent calculations
        totalClustersPF = float(elem.text)
    # Similar to above
    for element in runStatistics.iterfind("OverallSamples/SummarizedSampleStatistics"):
        # Iterate through the list of the various values defined above
        for dataL in dataList:
            for element1 in element.iter(dataL):
                # Append to elementData
                elementData.append(element1.text)
        percentperStrain = float(elementData[3]) / totalClustersPF * 100
        # Format the outputs to have two decimal places
        roundedPercentperStrain = ("%.2f" % percentperStrain)
        # Populate returnData with all the appropriate values
        # (Sample_ID, Sample_Name, Sample_Number are already in the dictionary. Add #clusterPF,
        # totalClustersPF, and % of total readsPF
        # strain = subdata[1].rstrip().replace(" ", "-").replace(".", "-").replace("---", "-").replace("--", "-").replace("=", "-").replace("+", "").replace("/", "-").replace("#", "")
        strain = elementData[2].rstrip().replace(" ", "-").replace(".", "-").replace("=", "-").replace("+", "").replace("/", "-").replace("#", "").replace("---", "-").replace("--", "-")
        passedMetadata[strain]["3.Run"]["SampleNumber"] = elementData[0]
        passedMetadata[strain]["3.Run"]["NumberOfClustersPF"] = elementData[3]
        passedMetadata[strain]["3.Run"]["TotalClustersinRun"] = totalClustersPF
        passedMetadata[strain]["3.Run"]["PercentOfClusters"] = roundedPercentperStrain
        # Clears the list for the next iteration
        elementData[:] = []
    return passedMetadata


def indexingQC(metadata):
    """In some cases, the GenerateFastqRunStatistics.xml file is not available. Equivalent data can be pulled from Basespace.
    Generate a text file  name indexingQC.txt containing the copied tables from the Indexing QC tab of the run on Basespace"""
    global IDs
    if os.path.isfile("indexingQC.txt"):
        with open("indexingQC.txt") as indexQC:
            for indexLine in indexQC:
                if re.search("Total", indexLine):
                    pass
                elif not re.search("Index", indexLine):
                    data = indexLine.split("\t")
                    PFreads = data[1]
                if re.search("Index", indexLine):
                    for subindex in indexQC:
                        for ID, strain in IDs:
                            if re.search(ID, subindex):
                                data = subindex.split("\t")
                                clustersPF = float(data[-1].rstrip()) * float(PFreads) / 100
                                roundedClustersPF = ("%.0f" % clustersPF)
                                metadata[strain]["3.Run"]["SampleNumber"] = data[0].rstrip()
                                metadata[strain]["3.Run"]["NumberOfClustersPF"] = roundedClustersPF
                                metadata[strain]["3.Run"]["TotalClustersinRun"] = PFreads
                                metadata[strain]["3.Run"]["PercentOfClusters"] = data[-1].rstrip()
    else:
        for ID, strain in IDs:
            metadata[strain]["3.Run"]["SampleNumber"] = "N/A"
            metadata[strain]["3.Run"]["NumberOfClustersPF"] = "N/A"
            metadata[strain]["3.Run"]["TotalClustersinRun"] = "N/A"
            metadata[strain]["3.Run"]["PercentOfClusters"] = "N/A"
    return metadata



def functionsGoNOW(path):
    """Run the functions"""
    parseRunInfo()
    date, metadata, sampleNames, fLength = parseSampleSheet()
    if os.path.isfile("%s/GenerateFASTQRunStatistics.xml" % path):
        moreMetadata = parseRunStats(metadata)
        return moreMetadata, sampleNames, date, fLength
    else:
        increasedMetadata = indexingQC(metadata)
        return increasedMetadata, samples, date, fLength
