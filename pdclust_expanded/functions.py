#!/usr/bin/env python

import functools
import os
import multiprocessing as mp
import glob
import time
import itertools
import sys
import pickle
import numpy as np
import scipy as sci
import pandas as pd
import subprocess
import pybedtools
from plotly import tools
import glob
#from Bio import SeqIO
import re
import gzip


######################################
def checkReferenceFiles(ref,ref_dir,partial=False):
    """
    Function for setting up required reference genome resources
    Example : checkReferenceFiles(Reference,Reference Directory,Partial)
    Returns CpG.bed,FREEC_contig_sizes.tsv, and invidual chromosome .fa
    """
    if not(os.path.isfile(ref_dir+"/"+ref+".fa")):
        print("Reference file "+ref_dir+"/"+ref+".fa does not exist.")
        print("Pipeline exiting. Please rerun after downloading reference file.")
        sys.exit(1)
    if not(os.path.isfile(ref_dir+"/"+ref+".CG.bed.gz")):
        print("Generating CpG file :"+ref_dir+"/"+ref+".CG.bed.gz")
        fasta_sequences = SeqIO.parse(open(ref_dir+"/"+ref+".fa"),'fasta')
        #file = open(ref_dir+"/"+ref+".CG.bed.gz,"w")
        file=gzip.open(ref_dir+"/"+ref+".CG.bed.gz","wb")
        for record in fasta_sequences:
                for match in re.finditer("CG|cg", str(record.seq)):
                    #entry="\t".join([record.name,match.span()[0],match.span()[1]])+"\n"
                    file.write(
                        ("\t".join([record.name,str(match.span()[0]),str(match.span()[1])])+"\n").encode()
                    )
        fasta_sequences.close()
        file.close()
    file=open(ref_dir+"/"+ref+".fa")
    first_line = file.readline().split(" ")[0].replace(">","")
    file.close()
    if not(os.path.isfile(ref_dir+"/"+first_line+".fa") and os.path.isfile(ref_dir+"/"+ref+"_freec_contig_sizes.tsv")):
        tracker=[]
        if partial:
            ### Assuming NCBI format
            print("Generating individual fa :"+ref_dir+"/"+ref+".fa")
            fasta_sequences = SeqIO.parse(open(ref_dir+"/"+ref+".fa"),'fasta')
            for record in fasta_sequences:
                    description = record.description
                    id = record.id
                    seq = record.seq
                    if "rl:Chromosome" in description:
                        id_file = open(ref_dir+"/"+id+".fa", "w")
                        id_file.write(">"+str(id)+"\n"+str(seq)+"\n")
                        id_file.close()
                        tracker.append([id,len(seq)])
        else:
            print("Generating individual fa :"+ref_dir+"/"+ref+".fa")
            fasta_sequences = SeqIO.parse(open(ref_dir+"/"+ref+".fa"),'fasta')
            for record in fasta_sequences:
                    description = record.description
                    id = record.id
                    seq = record.seq
                    id_file = open(ref_dir+"/"+id, "w")
                    id_file.write(">"+str(id)+"\n"+str(seq)+"\n")
                    id_file.close()
                    tracker.append([id,len(seq)])
        seq_length_file=open(ref_dir+"/"+ref+"_freec_contig_sizes.tsv", "w")
        for x in tracker:
            seq_length_file.write(x[0]+"\t"+str(x[1])+"\n")
        seq_length_file.close()
    print("Reference files exist!")
#######################################
def runPipeline(index_file,out_dir,ref,ref_dir,project_name,jobs=4,threads=16,force=False):
    """
    Pipeline wrapper function
    Example : runPipeline(readSingleCellIndex() Output,"/output directory/","reference_name","/ref/","JOB name",4,4)
    Outputs /log/job_status.tsv
    """
    #check if ref exists if not set up
    ###
    subprocess.run(["mkdir","-p","/out_dir/tmp"])
    os.environ['TMPDIR']="/out_dir/tmp"
    
    checkReferenceFiles(ref,ref_dir,True)
    csv_file=readSingleCellIndex(index_file,out_dir+"/",project_name,jobs,threads,ref)
    trimmed_files=setUpMetadata(index_file,out_dir+"/",project_name,jobs,threads,ref)
    file_tracker=pd.DataFrame(index=trimmed_files['file_id'].values.tolist())
    
    
    for x in file_tracker.index.values[::-1]:
        #print(x)
        file_tracker.loc[x,"original_fastqs_read1"]=",".join(csv_file.query("index=='"+x.split("_")[-1]+"'")['read1'].tolist())
        file_tracker.loc[x,"original_fastqs_read2"]=",".join(csv_file.query("index=='"+x.split("_")[-1]+"'")['read2'].tolist())
        file_tracker.loc[x,'trimmed_fastqs_read1']=",".join(trimmed_files.query("file_id==@x")['end_1'].tolist())
        file_tracker.loc[x,'trimmed_fastqs_read2']=",".join(trimmed_files.query("file_id==@x")['end_2'].tolist())
        file_tracker.loc[x,'aligned_bams']="/out_dir/mapping/"+x.split("_")[-1]+"/"+x.split("_")[-1]+".bam"
        file_tracker.loc[x,'fractional_meth']="/out_dir/extract/"+x.split("_")[-1]+"/"+x.split("_")[-1]+".fractional_methylation.bed.gz"
        file_tracker.loc[x,'coverage_track']="/out_dir/extract/"+x.split("_")[-1]+"/"+x.split("_")[-1]+".bw"
        file_tracker.loc[x,'meth_track']="/out_dir/extract/"+x.split("_")[-1]+"/"+x.split("_")[-1]+"_cpg.bb"
        file_tracker.loc[x,'cnv']="/out_dir/cnv/"+x.split("_")[-1]+"/"+x.split("_")[-1]+".dedup.bam_ratio.txt"
        
        
    ###Set up configuration files       
    if not (os.path.isfile(out_dir+"/log/job_status.csv")):
        print("Job manager not found. Making :"+out_dir+"/log/job_status.csv")
        file_status=file_tracker.copy()
        for x in file_status.columns.values.tolist():
            file_status[x]=["PENDING"]*len(file_status.index.values.tolist())
        gemBS_ConfigurationSetup(file_status.index.values.tolist(),out_dir+"/",project_name,jobs,threads,ref)
        freec_ConfigurationSetup(ref,[x.split("_")[-1] for x in file_status.index.values.tolist()],out_dir,project_name,jobs,threads)
    else:
        print("Job manager found - Resuming")
        file_status=pd.read_csv(out_dir+"/log/job_status.csv",sep=',',index_col=0)
        
        

    files=[
        [x.split("_")[-1],
         file_tracker.loc[x,'original_fastqs_read1'],
         file_tracker.loc[x,'original_fastqs_read2']
        ] for x in file_status.query("trimmed_fastqs_read1=='PENDING' and trimmed_fastqs_read2=='PENDING'").index.values.tolist()
          ]
        
    if len(files)>0:
        runTrimGalore(files,out_dir+"/",project_name,jobs,threads,ref)
        
        for files_to_check in ['trimmed_fastqs_read1','trimmed_fastqs_read2']:
            for x in file_status.query(files_to_check+"=='PENDING'").index.values.tolist():
                for file in file_tracker.loc[x,files_to_check].split(","):
                        if not (os.path.isfile(file)):
                            print("WARNING:"+file+" check failed. Halting operations.")
                            file_status.loc[x,files_to_check]='ERROR'
                            file_status.to_csv(out_dir+"/log/job_status.csv",sep=',')
                            #sys.exit(1)
                        else:
                            file_status.loc[x,files_to_check]="DONE"
        file_status.to_csv(out_dir+"/log/job_status.csv",sep=',')
                  
    if len(file_status.query("aligned_bams=='PENDING'"))>0:
        runGEMbs([x.split("_")[-1] for x in file_status.query("aligned_bams=='PENDING'").index.values.tolist()],
                 out_dir,
                 project_name,
                 jobs,threads,
                 ref)
        for files_to_check in ['aligned_bams','coverage_track',"meth_track"]:
            for x in file_status.query(files_to_check+"=='PENDING'").index.values.tolist():
                for file in file_tracker.loc[x,files_to_check].split(","):
                        if not (os.path.isfile(file)):
                            print("WARNING:"+file+" check failed. Halting operations.")
                            file_status.loc[x,files_to_check]='ERROR'
                            file_status.to_csv(out_dir+"/log/job_status.csv",sep=',')
                            #sys.exit(1)
                        else:
                            file_status.loc[x,files_to_check]="DONE"
        file_status.to_csv(out_dir+"/log/job_status.csv",sep=',')
                  
    if len(file_status.query("fractional_meth=='PENDING'"))>0:
        for files_to_check in ['fractional_meth']:
                calcFractionalMethylation(
                    [x.split("_")[-1] for x in file_status.query(files_to_check+"=='PENDING'").index.values.tolist()],
                    out_dir,
                    project_name,
                    ref,
                    jobs,
                    threads
                )
        for x in file_status.query(files_to_check+"=='PENDING'").index.values.tolist():
            for file in file_tracker.loc[x,files_to_check].split(","):
                if not (os.path.isfile(file)):
                    print("WARNING:"+file+" check failed. Halting operations.")
                    file_status.loc[x,files_to_check]='ERROR'
                    file_status.to_csv(out_dir+"/log/job_status.csv",sep=',')
                    sys.exit(1)
                else:
                    file_status.loc[x,files_to_check]="DONE"
        file_status.to_csv(out_dir+"/log/job_status.csv",sep=',')
            
    if len(file_status.query("cnv=='PENDING'"))>0:
        for files_to_check in ['cnv']:
            runControlFREEC([x.split("_")[-1] for x in file_status.query(files_to_check+"=='PENDING'").index.values.tolist()],
                            out_dir,
                            project_name,
                            ref,
                            jobs,
                            threads)
            for x in file_status.query(files_to_check+"=='PENDING'").index.values.tolist():
                for file in file_tracker.loc[x,files_to_check].split(","):
                    if not (os.path.isfile(file)):
                        print("WARNING:"+file+" check failed. Halting operations.")
                        file_status.loc[x,files_to_check]='ERROR'
                        file_status.to_csv(out_dir+"/log/job_status.csv",sep=',')
                        sys.exit(1)
                    else:
                        file_status.loc[x,files_to_check]="DONE"
        file_status.to_csv(out_dir+"/log/job_status.csv",sep=',')

                    


                    
    
    cmd=["rm","-r",out_dir+"/tmp"]
    runCommand([[out_dir,cmd,"make_temp",'run']])
    print("".join(["#"]*18)+"\nFinished") 

#######################################
def calcFractionalMethylation(indices,out_dir,project_name,ref,jobs=4,threads=16):
    """
    Function wrapper for converting out_dir/extract/**/*_cpg.bed.gz into strand collapsed out_dir/extract/**/*.fractional_methylation.bed.gz
    
    """
    print("".join(["#"]*18))
    print("Generating fractional methylation calls")
    t0=time.time()
    reference_cpgs="/ref/"+ref+".CG.bed.gz"
    
    cat_type = pd.api.types.CategoricalDtype(
    categories=pd.read_csv(
        reference_cpgs,
        names=['chr','start','stop'],
        usecols=['chr'],
        sep='\t',
        compression='gzip')['chr'].unique().tolist(),
    ordered=True)
    
    for x in indices:
        print("fractional Methylation "+out_dir+"/extract/"+x+"/"+x+"_cpg.bed.gz")
        fractionalMethylation(reference_cpgs,out_dir,out_dir+"/extract/"+x+"/"+x+"_cpg.bed.gz",cat_type)
    
    print("Run time:"+str(time.time()-t0))
#######################################
def fractionalMethylation(reference_cpgs,out_dir,cpg_file,cat_type):
    """
    Function for generating methylation calls from Basepair strand specific resolution gemBS output files.
    """
    known_CpG=pybedtools.BedTool(reference_cpgs)
    known_CpG.map(pybedtools.BedTool.from_dataframe(pd.read_csv(cpg_file,compression='gzip',skiprows=1,
                        names=[
                        "chr",
                        "start",
                        "stop",
                        "name",
                        "score",
                        "strand",
                        "display_start",
                        "display_end",
                        "color",
                        "coverage",
                        "methylation",
                        "ref_geno",
                        "sample_geno",
                        "quality_score"
                        ],
                        usecols=[
                            "chr",
                            "start",
                            "stop",
                            "coverage",
                            "methylation"
                        ],
                        sep='\t',
                       dtype={
                        "chr":str,
                        "start":int,
                        "stop":int,
                        "coverage":int,
                        "methylation":float 
                       }
                       )\
            .query("coverage>0")\
            .assign(methylated = lambda row : round(row["coverage"]*row["methylation"]/100))\
            .assign(unmethylated = lambda row : row["coverage"]-row['methylated'])\
            .assign(chr = lambda row : row['chr'].astype(cat_type))\
            .sort_values(["chr","start"])
                        
        ),
        c=[6,7])\
    .to_dataframe()\
    .rename(columns={"name":"methylated","score":"unmethylated"})\
    .query("methylated!='.' and unmethylated!='.'")\
    .replace(".",0)\
    .assign(coverage = lambda row : row['methylated'].astype(int)+row['unmethylated'].astype(int))\
    .assign(frac_meth = lambda row : round(row['methylated'].astype(int)/row['coverage'],2))\
    .to_csv(cpg_file.replace("_cpg.bed.gz",".fractional_methylation.bed.gz"),compression='gzip',sep='\t',index=False,header=False)
    pybedtools.cleanup()
            
######################################
def distributeJobs(jobs,total_cmd_list):
    """
    Function for running multiple jobs in multi-threadsd mode
    """
    pool = mp.Pool(jobs,maxtasksperchild=1)  
    pool.map(runCommand,total_cmd_list)
    pool.close()

##########################################
def runCommand(sample_cmd_list):
    """
    Function that accepts commands and executes saving log
    """
    
    for cmds in sample_cmd_list:
        out_dir=cmds[0]
        cmd=cmds[1]
        cmd_name=cmds[2]
        variable=cmds[3]
        
        print(" ".join(cmd))

        subprocess.run(["mkdir","-p",out_dir+"/log"])
        if variable=='shell':
            f=open(cmd[cmd.index(">")+1:][0], "w")
            result=subprocess.run(cmd[:cmd.index(">")],capture_output=True)
            f.write(result.stdout.decode('utf-8'));f.close()        
        elif variable=='log':
            f=open(out_dir+"/log/"+cmd_name+".stdout", "w")
            result=subprocess.run(cmd,capture_output=True)
            f.write(result.stdout.decode('utf-8'));f.close()

            f=open(out_dir+"/log/"+cmd_name+".stderr", "w")
            f.write(result.stderr.decode('utf-8'));f.close()
        elif variable=='run':
            subprocess.run(cmd)
        else:
            pass
            
            
#######################################
def runGEMbs(indices,out_dir,project_name,jobs,threads,ref):
    """
    Wrapper function for gemBS. Runs All gemBs with dup marking and flagstat
    """
    print("".join(["#"]*18))
    t0=time.time()
    cmd=["gemBS","prepare","-c",out_dir+"config.txt","-t",out_dir+"metadata.csv"]
    runCommand([[out_dir,cmd,"gemBS_prepare",'log']])
    
    cmd=["gemBS","index"]
    runCommand([[out_dir,cmd,"gemBS_index",'log']])

    
    cmd=["gemBS","--loglevel","debug","map"]
    runCommand([[out_dir,cmd,"gemBS_map",'log']])
    

    cmd=["gemBS","--loglevel","debug","merge-bams"]
    runCommand([[out_dir,cmd,"gemBS_merge",'log']])
    
    total_cmd_list=[]
    for x in indices:
        sample_cmd_list=[]
        cmd=[
        "java",
        "-Xmx4g",
        "-jar",
        '/usr/local/anaconda/share/picard-2.22.3-0/picard.jar',
        'MarkDuplicates',
        "I="+"/out_dir/mapping/"+x+"/"+x+".bam",
        "O="+"/out_dir/mapping/"+x+"/"+x+".dups.marked.sorted.bam",
        "M="+"/out_dir/mapping/"+x+"/"+x+"_metrics.txt",
        "VALIDATION_STRINGENCY=SILENT",
        "ASSUME_SORTED=true",
        "TMP_DIR="+out_dir+"/mapping/tmp"
        ]
        
        sample_cmd_list.append([out_dir,cmd,"markDup_"+x,'log'])
        
        cmd=["mv",
             "/out_dir/mapping/"+x+"/"+x+".dups.marked.sorted.bam",
             "/out_dir/mapping/"+x+"/"+x+".bam"]
        sample_cmd_list.append([out_dir,cmd,"mv_"+x,'log'])

        cmd=["md5sum","/out_dir/mapping/"+x+"/"+x+".bam",">","/out_dir/mapping/"+x+"/"+x+".bam.md5sum"]
        sample_cmd_list.append([out_dir,cmd,"recalc_md5sum",'shell'])

        cmd=["samtools",
             "flagstat",
             "-@"+str(threads),
             "/out_dir/mapping/"+x+"/"+x+".bam",
             ">",
             "/out_dir/mapping/"+x+"/"+x+".flagstat"]
        sample_cmd_list.append([out_dir,cmd,"flagstat",'shell'])

        cmd=["samtools","index","-c","-@"+str(threads),"/out_dir/mapping/"+x+"/"+x+".bam"]
        sample_cmd_list.append([out_dir,cmd,"csi",'run'])
        
        total_cmd_list.append(sample_cmd_list)
    
    distributeJobs(jobs,total_cmd_list)
        
    cmd=["gemBS","--loglevel","debug","call"]
    runCommand([[out_dir,cmd,"gemBS_call",'log']])

    cmd=["gemBS","--loglevel","debug","extract"]
    runCommand([[out_dir,cmd,"gemBS_extract",'log']])
    print("Run time:"+str(time.time()-t0))
    

#######################################
def runTrimGalore(indices,out_dir,project_name,jobs,threads,ref):
    """
    Function for running trimgalore on reads
    """
    print("".join(["#"]*18))
    t0=time.time()
    total_cmd_list=[]
    cmd=['mkdir','-p',out_dir+"/fastq/"]
    runCommand([[out_dir,cmd,"making_fastq_dir","run"]])
    for x in indices:
        cmd=["trim_galore","--clip_R1","6","--clip_R2","6","--paired",x[1],x[2],"-o",out_dir+"/fastq/","-j",str(threads),"--gzip","--fastqc"]
        total_cmd_list.append([[out_dir,cmd,"trim_"+x[0],"log"]])
    
    distributeJobs(jobs,total_cmd_list)
    print("Run time:"+str(time.time()-t0))

#######################################
def setUpMetadata(csv_file,out_dir,project_name,jobs,threads,ref):
    """
    Function for setting up metadata necessary for gemBS
    """
    print("".join(["#"]*18))
    print("SETTING UP metadata.csv")
    tmp=pd.read_csv(csv_file,sep='\t',names=['index','read1','read2'])\
    .assign(end_1 = lambda row : out_dir+"/fastq/"+row['read1'].str.split("/").str[-1].str.replace(".fastq.gz","_val_1.fq.gz"))\
    .assign(end_2 = lambda row : out_dir+"/fastq/"+row['read2'].str.split("/").str[-1].str.replace(".fastq.gz","_val_2.fq.gz"))\
    .assign(Barcode = lambda row : row['index'])\
    .assign(Library = lambda row : project_name)\
    .assign(file_id = lambda row : row['Library']+"_"+row['index'])\
    .assign(file_name = lambda row : row['Library']+"_"+row['index'])\
    .loc[:,["Barcode","Library","file_id","end_1","end_2","file_name"]]
    tmp.to_csv(out_dir+"/metadata.csv",sep=',',index=False)
    return(tmp)
########################################
def readSingleCellIndex(index_file_path,out_dir,project_name,jobs,threads,ref):
    """
    Verify index file format and read paths
    """
    print("".join(["#"]*18))

    csv_file=None
    read_error=False
    if os.path.exists(index_file_path):
        try:
            csv_file=pd.read_csv(index_file_path,sep='\t',names=["index","read1","read2"])
            if (csv_file.shape[0]>1) and (csv_file.shape[1]>=3):
                print("Format looks good. Continuing")
            else:
                print("Format Error")
                sys.exit(1)
        except:
            print("Problem openning {}. Check file path or if file is in appropriate format".format(index_file_path))
    else:
        print("{} does not exist".format(index_file_path))
        sys.exit(1)  
    
    for x in csv_file['read1'].values.tolist()+csv_file['read2'].values.tolist():
        if not os.path.exists(x):
            print("{} does not exist".format(x))
            read_error=True
    
    if read_error:
        print("Issues persist. Please address them and run again")
        sys.exit(1)
        
    return(csv_file)


############################################
def gemBS_ConfigurationSetup(index_file,out_dir,project_name,jobs,threads,ref):
    """
    Function for setting up config options necessary for gemBS
    """
    print("".join(["#"]*18))
    print("SETITNG UP config.txt")
    f=open(out_dir+"/config.txt","w+")
    ### RUNNING PARAMETERS
    ### AS PER IHEC STANDARDS ; SEE GITHUB IF CHANGES ARE NECESSARY
    ### WORKING DIRECTORY - mounted ###
    f.write(
    "base="+out_dir+" ### if mounted by following example do not change ###"+"\n"+\
    ""+"\n"+\
    "sequence_dir = ${base}/fastq/@SAMPLE    # @SAMPLE and @BARCODE are special"+"\n"+\
    "bam_dir = ${base}/mapping/@BARCODE      # variables that are replaced with"+"\n"+\
    "bcf_dir = ${base}/calls/@BARCODE        # the sample name or barcode being"+"\n"+\
    "extract_dir = ${base}/extract/@BARCODE  # worked on during gemBS operation"+"\n"+\
    "report_dir = ${base}/report"+"\n"+\
    ""+"\n"+\
    "### REFERENCES - mounted ###"+"\n"+\
    "### if mounted by following example do not change ###"+"\n"+\
    "reference = /ref/"+ref+".fa"+"\n"+\
    "index_dir = /ref"+"\n"+\
    "extra_references = /ref/conversion_control.fa"+"\n"+\
    ""+"\n"+\
    "# General project info"+"\n"+\
    "project = "+project_name+" ### SPECIFIC PROJECT TITLE ###"+"\n"+\
    "species = hg38"+"\n"+\
    ""+"\n"+\
    "# Default parameters"+"\n"+\
    "threads = "+str(threads)+"\n"+\
    "jobs = "+str(jobs)+" ### MODIFY FOR NEED BE ###"+"\n"+\
    ""+"\n"+\
    "[index]"+"\n"+\
    ""+"\n"+\
    "sampling_rate = 4"+"\n"+\
    ""+"\n"+\
    "[mapping]"+"\n"+\
    ""+"\n"+\
    "non_stranded = True ### TOGGLE TO TRUE FOR PBAL ###"+"\n"+\
    "remove_individual_bams = True"+"\n"+\
    "underconversion_sequence = NC_001416.1"+"\n"+\
    "overconversion_sequence = V01146.1"+"\n"+\
    ""+"\n"+\
    "[calling]"+"\n"+\
    ""+"\n"+\
    "mapq_threshold = 10"+"\n"+\
    "qual_threshold = 13"+"\n"+\
    "reference_bias = 2"+"\n"+\
    "left_trim = 0"+"\n"+\
    "right_trim = 0"+"\n"+\
    "keep_improper_pairs = True ### TOGGLE TO TRUE FOR PBAL ###"+"\n"+\
    "keep_duplicates = False ### TOGGLE TO TRUE FOR RRBS -  ###"+"\n"+\
    "haploid = False"+"\n"+\
    "conversion = auto"+"\n"+\
    "remove_individual_bcfs = True"+"\n"+\
    "contig_pool_limit = 25000000"+"\n"+\
    ""+"\n"+\
    "[extract] # extract specific section"+"\n"+\
    ""+"\n"+\
    "strand_specific = True"+"\n"+\
    "phred_threshold = 10"+"\n"+\
    "make_cpg = True"+"\n"+\
    "make_non_cpg = False"+"\n"+\
    "make_bedmethyl = True"+"\n"+\
    "make_bigwig = True"+"\n"
    )
    f.close()
############################################
def freec_ConfigurationSetup(ref,indices,out_dir,project_name,jobs=4,threads=16):
    """
    Function for setting up config options necessary for ControlFREEC
    """
    print("".join(["#"]*18))
    print("SETITNG UP freec configs")            
    cmd=['mkdir','-p',out_dir+"/cnv"]
    runCommand([[out_dir,cmd,"making_cnv_dir","run"]])
            
    for x in indices:
        cmd=['mkdir','-p',out_dir+"/cnv/"+x]
        runCommand([[out_dir,cmd,"mkdir_cnv","run"]])
        
        f=open(out_dir+"/cnv/"+x+"/config.txt","w+")
        f.write(
        "[general]"+"\n"+\
        "chrFiles=/ref"+"\n"+\
        "chrLenFile=/ref/"+ref+"_freec_contig_sizes.tsv"+"\n"+\
        "maxThreads="+str(threads)+"\n"+\
        "ploidy=2"+"\n"+\
        "samtools=//usr/local/anaconda/bin/samtools"+"\n"+\
        "window=5000000"+"\n"+\
        "telocentromeric=5000000"+"\n"+\
        "outputDir="+out_dir+"/cnv/"+x+"\n"+\
        "sex=XY"+"\n"+\
        "minExpectedGC=0.39"+"\n"+\
        "maxExpectedGC=0.51"+"\n"+\
        "\n"+\
        "[sample]"+"\n"+\
        "mateFile="+out_dir+"/cnv/"+x+"/"+x+".dedup.bam"+"\n"+\
        "inputFormat=BAM\n"
        )
        f.close()
    
############################################
def runControlFREEC(indices,out_dir,project_name,ref,jobs=4,threads=16):
    """
    Wrapper for CNV calling via ControlFreec
    """
    print("".join(["#"]*18))
    t0=time.time()   
    total_cmd_list=[]
    for x in indices:
        sample_cmd_list=[]
        bam=out_dir+"/mapping/"+x+"/"+x+".bam"
        cmd=["samtools",
             "view",
             bam,
             "-@"+str(threads),
             "-h",
             "-b",
             "-F516",
             "-o",
             bam.replace(".bam",".dedup.bam").replace("mapping","cnv")
        ]

        sample_cmd_list.append([out_dir,cmd,"dedup_"+x,"run"])
        cmd=["freec","-conf",out_dir+"/cnv/"+x+"/config.txt"]

        sample_cmd_list.append([out_dir,cmd,"freec_"+x,"log"])

        cmd=["rm",
             bam.replace(".bam",".dedup.bam").replace("mapping","cnv")
        ]
        sample_cmd_list.append([out_dir,cmd,"rm_"+x+"_dedup","run"])
        total_cmd_list.append(sample_cmd_list)
    
    distributeJobs(jobs,total_cmd_list)
    print("Run time:"+str(time.time()-t0))
############################################
        

