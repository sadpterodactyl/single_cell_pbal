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
import plotly
import plotly.graph_objs as go
import json
import re
from scipy.stats import pearsonr
from scipy.cluster import hierarchy
from scipy.spatial import distance
import scipy
#import umap
from sklearn.manifold import MDS
from sklearn.decomposition import PCA
from plotly import tools
import statsmodels.stats.multitest as multitest
from rpy2 import robjects as ro
from rpy2.robjects import r,pandas2ri 
from rpy2.robjects.packages import importr

from functools import reduce

########################################
def plot_figure(fig,out_dir,file_name):
    """
    Function for plotting figures in fixed dimensions and scale
    Example : plot_figure(methylation_figure,'/outdirectory/',methylation_boxplot.png)
    Returns Png saved to /results/
    """
    print("Running : Plotting "+file_name)
    t0 = time.time()
    os.environ['TMPDIR']="/out_dir/tmp"
    plot_dim_x=800
    plot_dim_y=800
    #plotly.offline.iplot(fig,validate=False, filename='customizing-subplot-axes')
    
    fig.write_image(out_dir+"/results/"+file_name+".png",width=plot_dim_x,height=plot_dim_y,scale=10)
    pickle.dump(fig, open(out_dir+"/results/"+file_name+".pkl", "wb" ) )
    print(time.time()-t0)

#########################################
def findFiles(list_of_files,out_dir,project_name):
    """
    Function retrieves required outputs for downstream analysis.
    Example : findFiles(["SAMPLE_1","SAMPLE_2","SAMPLE_3"],"/outdirectory","super_special_project")
    Returns dataframe of files per sample
    """
    print("Running : Scanning for files")
    t0 = time.time()
    subprocess.run(["mkdir","-p","/out_dir/tmp"])
    os.environ['TMPDIR']="/out_dir/tmp"
    #subprocess.run(["TMPDIR"+"="+out_dir+"/tmp"])
    pandas2ri.activate()
    plotly.io.orca.ensure_server()
    time.sleep(10)

    tmp=pd.DataFrame()
    tmp['bam']=list_of_files
    tmp['sample']=[x.split("/")[-1].replace(".bam","") for x in list_of_files]
    tmp['flagstat']=[x.replace("bam","flagstat") for x in list_of_files]
    tmp['cpg']=[x.replace("mapping","extract").replace("bam","fractional_methylation.bed.gz") for x in list_of_files]
    tmp['cnv']=[x.replace("mapping","cnv").replace("bam","dedup.bam_ratio.txt") for x in list_of_files]
    tmp.set_index("sample",inplace=True)
    tmp['json']=[out_dir+"/mapping/"+x.split("/")[-1].replace(".bam","")+"/"+project_name+"_"+x.split("/")[-1].replace("bam","json") for x in list_of_files]
    tmp['sample']=tmp.index.values.tolist()
    for x in [x for x in tmp.columns.values.tolist() if "sample"!=x]:
        for y in tmp[x].values.tolist():
            if not(os.path.isfile(y)):
                print("{} does not exist".format(y))
    print(time.time()-t0)
    return(tmp)
###########################################
def pullStatistics(files,chr_list):
    """
    Function computes statistics on given file set and chromosome set
    Example : pullStatistics(".findFiles() output",['chr1','chr2'])
    Returns dataframe of stats per sample
    """
    print("Running : Pulling QC statistics")
    t0 = time.time()
    tmp=pd.DataFrame()
    tmp['sample']=files['sample']
    tmp=tmp.set_index("sample")
    
    ###Retrieve basic alignment statistics
    tmp['total_reads']=None
    for sample,flagstat in files.loc[:,["sample","flagstat"]].values.tolist():
        
        cmd="grep 'in total (QC-passed reads + QC-failed reads)' "+flagstat+" | cut -f1 -d' '"
        
        result=int(subprocess.run(cmd,shell=True,stdout=subprocess.PIPE, check=True)\
        .stdout.decode('utf-8'))
        tmp.loc[sample,"total_reads"]=result

    tmp['mapped']=None
    for sample,flagstat in files.loc[:,["sample","flagstat"]].values.tolist():
        
        cmd="grep 'mapped (' "+flagstat+" | cut -f1 -d' '"
        
        result=int(subprocess.run(cmd,shell=True,stdout=subprocess.PIPE, check=True)\
        .stdout.decode('utf-8'))
        tmp.loc[sample,"mapped"]=result

    tmp['duplicates']=None
    for sample,flagstat in files.loc[:,["sample","flagstat"]].values.tolist():
        
        cmd="grep 'duplicates' "+flagstat+" | cut -f1 -d' '"
        
        result=int(subprocess.run(cmd,shell=True,stdout=subprocess.PIPE, check=True)\
        .stdout.decode('utf-8'))
        tmp.loc[sample,"duplicates"]=result
        
    ###Retrieve CpG statistics
    tmp['cpg_count']=None
    tmp['cpg_count_cov3']=None
    tmp['average_meth']=None
    tmp['average_meth_cov3']=None
    for sample,cpg in files.loc[:,["sample","cpg"]].values.tolist():
        
        cpg_quick=pd.read_csv(cpg,names=['chr','start','end','meth_cov','unmeth_cov','cov','meth_frac'],compression='gzip',sep='\t')\
        .loc[:,["cov","meth_frac"]]
        
        tmp.loc[sample,"cpg_count"]=len(cpg_quick)
        tmp.loc[sample,"cpg_count_cov3"]=len(cpg_quick.query("cov>=3"))
        tmp.loc[sample,"average_meth"]=cpg_quick.meth_frac.mean()
        tmp.loc[sample,"average_meth_cov3"]=cpg_quick.query("cov>=3").meth_frac.mean()
        del cpg_quick
        
    ###Calculate Percentages
    tmp['mapped%']=tmp['mapped']/tmp['total_reads']*100
    tmp['dup_rate%']=tmp['duplicates']/tmp['mapped']*100
    tmp['mapped_minus_dup']=tmp['mapped']-tmp['duplicates']
    tmp['mapped_minus_dup%']=tmp['mapped_minus_dup']/tmp['total_reads']*100
    
    def getConversionRate(file):
        """
        Code courtesy of Simon Heath
        """
        with open(file) as outfile:
            data=json.load(outfile)
            output=[]
            for query in ['General',"OverConversionControl","UnderConversionControl"]:
                if query+'C2T' not in data["BaseCounts"]:
                  output.append(np.NaN);continue
                if query+'G2A' not in data["BaseCounts"]:
                  output.append(np.NaN);continue  
                #A
                a_bp_pair_one = data["BaseCounts"][query+'C2T']['A'][0] + data["BaseCounts"][query+'G2A']['A'][0]
                a_bp_pair_two = data["BaseCounts"][query+'C2T']['A'][1] + data["BaseCounts"][query+'G2A']['A'][1]
                #C
                c_bp_pair_one = data["BaseCounts"][query+'C2T']['C'][0] + data["BaseCounts"][query+'G2A']['C'][0]
                c_bp_pair_two = data["BaseCounts"][query+'C2T']['C'][1] + data["BaseCounts"][query+'G2A']['C'][1]
                #G
                g_bp_pair_one = data["BaseCounts"][query+'C2T']['G'][0] + data["BaseCounts"][query+'G2A']['G'][0]
                g_bp_pair_two = data["BaseCounts"][query+'C2T']['G'][1] + data["BaseCounts"][query+'G2A']['G'][1]
                #T
                t_bp_pair_one = data["BaseCounts"][query+'C2T']['T'][0] + data["BaseCounts"][query+'G2A']['T'][0]
                t_bp_pair_two = data["BaseCounts"][query+'C2T']['T'][1] + data["BaseCounts"][query+'G2A']['T'][1]

                n1 = float(a_bp_pair_one + g_bp_pair_one + c_bp_pair_two + t_bp_pair_two)
                n2 = float(c_bp_pair_one + t_bp_pair_one + a_bp_pair_two + g_bp_pair_two)
                #if (n1 + n2) < 10000:
                #    output.append(np.NaN);continue
                if n1 == 0:
                    output.append(0);continue

                z = float(a_bp_pair_one + t_bp_pair_two) / float(a_bp_pair_one + g_bp_pair_one + c_bp_pair_two + t_bp_pair_two)
                a = (t_bp_pair_one + a_bp_pair_two) * float(1.0 - z) - (c_bp_pair_one + g_bp_pair_two) * z 
                b = (t_bp_pair_one + a_bp_pair_two + c_bp_pair_one + g_bp_pair_two) * float(1.0 - z)
                output.append(float(a)/float(b)*100)
            del data
            return(output)

    ###Retrieve conversion rates
    tmp['T7_conversion']=None
    tmp['lambda_conversion']=None
    tmp['general_conversion']=None
    for sample,json_file in files.loc[:,["sample","json"]].values.tolist():
        tmp.loc[sample,"general_conversion"],tmp.loc[sample,"T7_conversion"],tmp.loc[sample,"lambda_conversion"]=getConversionRate(json_file)
        
        
    ###Retrieve CNV calls
    tmp['CNV']=None
    
    cnv_list=[]
    tmp_chr_list=[x.replace("chr","") for x in chr_list]
    for sample,cnv in files.loc[:,['sample',"cnv"]].values.tolist():
        cnv_list.append(pd.read_csv(cnv,sep='\t',dtype={'Chromosome':'category','Start':float,'Ratio':float,'MedianRatio':float,'CopyNumber':float})\
        .assign(Chromosome = lambda row : pd.Categorical(row['Chromosome'],categories=tmp_chr_list,ordered=True))\
        .assign(Ratio= lambda row : np.where(row['Ratio'] > 3,3,row['Ratio']))\
        .assign(Ratio= lambda row : np.where(row['Ratio'] < 0,0,row['Ratio']))\
        .assign(MedianRatio= lambda row : np.where(row['MedianRatio'] > 3,3,row['MedianRatio']))\
        .assign(MedianRatio= lambda row : np.where(row['MedianRatio'] < 0,0,row['MedianRatio']))\
        .assign(CopyNumber= lambda row : np.where(row['CopyNumber'] >5,5,row['CopyNumber']))\
        .loc[:,["Chromosome","Start","CopyNumber"]]\
        .rename(columns={"CopyNumber":sample}).set_index(["Chromosome","Start"])
                       )
        
    CNV_array=pd.concat(cnv_list,axis=1,join='outer')\
    .reset_index(level="Start")\
    .reset_index(level="Chromosome")\
    .assign(Chromosome = lambda row : pd.Categorical(row['Chromosome'],categories=tmp_chr_list,ordered=True))\
    .sort_values(['Chromosome','Start']).query("Chromosome==@tmp_chr_list")
    CNV_count=CNV_array[CNV_array.iloc[:,2:]!=2.0].count()
    
    for sample in files.loc[:,['sample']].values.tolist():
        tmp.loc[sample,'CNV']=CNV_count[sample]
        
    print(time.time()-t0)
    return(tmp,CNV_array)

##########################################################################
def plotBoxplot(what_to_plot,stats,annotation,job_name,shared_axes=True):
    """
    Generic boxplot function.
    Example : plotBoxplot("methylation",dataframe_containing_methylation,"Project Name")
    Returns figure instance
    """
    print("Running : Generating Boxplot")
    t0 = time.time()
    colors=[
        '#1f77b4',#  // muted blue
        '#ff7f0e',#  // safety orange
        '#2ca02c',#  // cooked asparagus green
        '#d62728',#  // brick red
        '#9467bd',#  // muted purple
        '#8c564b',#  // chestnut brown
        '#e377c2',#  // raspberry yogurt pink
        '#7f7f7f',#  // middle gray
        '#bcbd22',#  // curry yellow-green
        '#17becf'#   // blue-teal
    ]

    fig =plotly.subplots.make_subplots(
        rows=1,
        cols=len(stats.annotation.unique().tolist()),
        shared_yaxes=shared_axes,
        subplot_titles=stats.annotation.unique().tolist())
    count=0
    for z in annotation:
        for y in stats[z].unique().tolist():
            count+=1
            showlegend=True
            if count > 1 : showlegend = False
            for x,color in zip(what_to_plot,colors[:len(what_to_plot)]):
                fig.append_trace(go
                                 .Box(
                                     y = stats.query(z+"==@y").loc[:,x].values.tolist(),
                                     name=x,
                                     jitter = 0.5,
                                     boxpoints='all',
                                     pointpos = -2,
                                     marker=dict(symbol='circle-open',opacity=1,size=15,color=color),
                                     line=dict(width=1),
                                     whiskerwidth=1,
                                     showlegend=showlegend,
                                     hoverinfo='none'
                                 ),1,count
                )
                fig['layout']['xaxis'+str(count)].update(showticklabels=False)
                fig['layout']['yaxis'+str(count)].update(gridcolor='lightgrey')


    for z in fig['layout']['annotations']:
        z['y']=-0.05
                     
    fig['layout'].update(
        width=800,
        height=800,
        title=job_name+" "+",".join(what_to_plot),
        showlegend=True,
        titlefont=dict(size=20),
        legend=go.layout.Legend(orientation='h',font=dict(size=20)),
        paper_bgcolor='rgb(255,255,255)',
        plot_bgcolor='rgb(255,255,255)'
    )
    print(time.time()-t0)
    return(fig)
#####################################################
def plot_dendrogram_CNV(CNV,stats,annotations,annotations_category_colored,cut_tree):
    """
    Function clusters single cells by CNV in euclidean space
    Example : plot_dendrogram_CNV(.pullStatistics() CNV Output,.pullStatistics() statistics Output,['Methylation',"Example Annotation"],.ready_annotations() output, Number Of Tree clusters)
    Returns figure instance and single cell CNV groupings
    ### Developers note bit of a weird bug ongoing right where if CNV rows less than 10, legend position breaks
    """
    print("Running : Generating CNV dendrogram")
    t0 = time.time()
    ###Define spacing#####################################
    ###End goal is nested array where inner array is row, outer is col
    per_row_specs=[]
    row_specs=None
    total_specs=[]
    anno_row_size=None
    filler=None
    annotation_size=None
    
    CNV_windows=CNV.groupby("Chromosome").count().sort_values("Start",ascending=False).Start.values.tolist()
    ### establish rows first
    row_specs=len(CNV.iloc[:,2:].columns.values.tolist())+2+len(annotations) ###length of CNV + annotations (default extends by one for CNV)
    for x in CNV_windows:
        per_row_specs.append({"rowspan":row_specs-(2+len(annotations)),"colspan":x})
        per_row_specs.append(None) ###leave one col space between each chr and dendro
        for y in range(1,x):
            per_row_specs.append(None)

    ### Add in spacing for annotations
    annotation_size=int(CNV_windows[1]/3)
    for x in range(0,len(annotations)+1):
        for x in range(0,annotation_size):
            per_row_specs.insert(0,None)
        per_row_specs.insert(0,{"rowspan":row_specs-(2+len(annotations)),"colspan":annotation_size})

    ### Add in spacing for dendrogram
    for x in range(0,annotation_size*3):
         per_row_specs.insert(0,None)

    per_row_specs.insert(0,{"rowspan":row_specs-(2+len(annotations)),"colspan":annotation_size*3})

    ###fill in gaps in body cols
    total_specs.append(per_row_specs)
    for x in range(1,row_specs-(2+len(annotations))):
         total_specs.append([None]*len(per_row_specs))
    
    
    ### Add spaces for annotations legends
    anno_row_size=int(len(total_specs)/10)
    filler=int(len(per_row_specs)/4-1)
    
    for y in range(0,anno_row_size):
        total_specs.append([None]*len(per_row_specs))

    for x in range(0,len(annotations)+1):
        total_specs.append(
            [None]*filler+[{"rowspan":anno_row_size,"colspan":len(per_row_specs)-filler*2}]+[None]*(len(per_row_specs)-filler-1)
        )
        for y in range(0,anno_row_size*2):
            total_specs.append([None]*len(per_row_specs))
        
    total_specs.append(
        [None]*filler+[{"rowspan":anno_row_size,"colspan":len(per_row_specs)-filler*2}]+[None]*(len(per_row_specs)-filler-1)
    )
    for y in range(0,anno_row_size):
            total_specs.append([None]*len(per_row_specs))

    ###Define dendogram#####################################    
    Z = scipy.cluster.hierarchy.linkage(1-CNV.iloc[:,2:].corr(), 'ward')
    dn = scipy.cluster.hierarchy.dendrogram(Z, labels = CNV.iloc[:,2:].corr().columns,no_plot=True)
    icoord = dn['icoord']
    dcoord = dn['dcoord']
    ordered_labels = dn['ivl']
    
    #########Get dendrogram end points and labels
    yvals_flat=[item for sublist in dcoord for item in sublist]
    xvals_flat =[item for sublist in icoord for item in sublist]
    zero_vals = []
    for i in range(len(yvals_flat)):
        if yvals_flat[i] == 0.0 and xvals_flat[i] not in zero_vals:
            zero_vals.append(xvals_flat[i])

    if len(zero_vals) > len(dcoord) + 1:
            l_border = int(min(zero_vals))
            r_border = int(max(zero_vals))
            correct_leaves_pos = range(l_border,
                                       r_border + 1,
                                       int((r_border - l_border) / len(dcoord)))
            # Regenerating the leaves pos from the self.zero_vals with equally intervals.
            zero_vals = [v for v in correct_leaves_pos]

    zero_vals.sort()
    #########Define dendrogram groups
    cutree = hierarchy.cut_tree(Z, n_clusters=cut_tree)
    cnv_clusters=pd.DataFrame(index=CNV.iloc[:,2:].columns.tolist())
    cnv_clusters['cnv_clusters']=[chr(x[0]+65) for x in cutree]
    
    #########Alter annotation dictionary for CNV scale and CNV groups
    annotations_category_colored['cnv_clusters']={}
    annotations_category_colored['cnv_clusters']['type']='string'
    colorlist=annotations_category_colored['annotation_colors'].pop(0)
    unique=cnv_clusters.loc[:,'cnv_clusters'].unique().tolist()
    if len(unique)<=2:
        num_list=[0,1]
    else:
        num_list=list(np.arange(0,1,1/(len(unique)-1)))
        num_list.append(1)
    for yanno,ycolor,ynum in zip(unique,colorlist[:len(unique)],num_list):
            annotations_category_colored['cnv_clusters'][yanno]={}
            annotations_category_colored['cnv_clusters'][yanno]['num']=ynum
            annotations_category_colored['cnv_clusters'][yanno]['color']=ycolor

            
    ###initialize plotting frame###########################################
    fig = plotly.subplots.make_subplots(rows=len(total_specs),
                              cols=len(total_specs[0]),
                              subplot_titles=[""]+annotations+["CNV_cluster"]+CNV.Chromosome.unique().tolist(),
                              specs=total_specs,
                              print_grid=False
                             )
    
    ###Return plotting frame coordinates and axis names###########################################
    
    icount=0
    indices=[]
    m=re.findall(r"\(.*?y[0-9]*",fig._grid_str.replace("(empty)",""))
    for x in m:
        tmp={}
        tmp['xaxis_num']=int(x.split(" ")[0].split(",")[0].replace("(",""))
        tmp['yaxis_num']=int(x.split(" ")[0].split(",")[-1].replace(")",""))
        tmp['xaxis_name']="xaxis"+x.split(" ")[1].split(",")[0][1:]
        tmp['yaxis_name']="yaxis"+x.split(" ")[1].split(",")[1][1:]
        indices.append(tmp)

    ###plot nodes to vertical dendrogram
    for label,dcoor,icoor in zip(ordered_labels,dcoord,icoord):
        fig.append_trace(
            go.Scatter(
                x=dcoor,
                y=icoor,
                mode='lines',
                text=ordered_labels,
                marker=dict(color='black'),
                showlegend=False
            ),indices[icount]['xaxis_num'],indices[icount]['yaxis_num'])
    
    fig.layout[indices[icount]['xaxis_name']].update(
        autorange='reversed',
        tickangle=45,
        gridcolor='lightgrey')
    fig.layout[indices[icount]['yaxis_name']].update(
        ticktext=ordered_labels,
        tickfont=dict(size=8),
        tickvals=zero_vals,
        showticklabels=False,
        ticks="",range=(min(zero_vals)-min(zero_vals),max(zero_vals)+min(zero_vals)),
        side='right')
    icount+=1;
    
    ###Plot vertical annotations
    for xanno in annotations+['cnv_clusters']:
        if annotations_category_colored[xanno]["type"]=="numeric":
            tmp=stats.loc[ordered_labels,xanno]
            fig.append_trace(
            go.Heatmap(
                z=[[x]for x in tmp.values.tolist()],
                y=tmp.index.values.tolist(),
                zmin=annotations_category_colored[xanno]['cmin'],
                zmax=annotations_category_colored[xanno]['cmax'],
                colorscale=annotations_category_colored[xanno]['color'],
                showscale=False
            ),indices[icount]['xaxis_num'],indices[icount]['yaxis_num']
            )
        else:
            if xanno!='cnv_clusters':
                tmp=stats.loc[ordered_labels,xanno]
            else:
                tmp=cnv_clusters.loc[ordered_labels,"cnv_clusters"]
                
            for yanno in [*annotations_category_colored[xanno].keys()][1:]:
                tmp=tmp.replace(yanno,annotations_category_colored[xanno][yanno]['num'])
            
            colorscale=[[annotations_category_colored[xanno][z]['num'],annotations_category_colored[xanno][z]['color']] for z in [*annotations_category_colored[xanno].keys()][1:]]
            if len(colorscale)==1:
                colorscale.insert(0,[0,colorscale[0][1]])
            fig.append_trace(
            go.Heatmap(
                z=[[x]for x in tmp.values.tolist()],
                y=tmp.index.values.tolist(),
                colorscale=colorscale,
                showscale=False
            ),indices[icount]['xaxis_num'],indices[icount]['yaxis_num']
            )
            
        fig['layout'][indices[icount]['yaxis_name']].update(showticklabels=False,ticks="")
        fig['layout'][indices[icount]['xaxis_name']].update(showticklabels=False,ticks="")
        icount+=1; 
    
    ####Plot CNV heatmaps
    cnv_max=max(CNV.iloc[:,2:].max())
    cnv_min=0
    for x in CNV.Chromosome.unique().tolist():
        fig.append_trace(
        go.Heatmap(
            z=CNV.query("Chromosome==@x").loc[:,ordered_labels].T.values.tolist(),
            y=CNV.loc[:,ordered_labels].columns.values.tolist(),
            zmin=cnv_min,
            zmax=cnv_max,
            showscale=False,
            colorscale="RdBu"
        ),indices[icount]['xaxis_num'],indices[icount]['yaxis_num']
        )
        fig['layout'][indices[icount]['yaxis_name']].update(showticklabels=False,ticks="")
        fig['layout'][indices[icount]['xaxis_name']].update(showticklabels=False,ticks="")
        icount+=1;
    
    
    ### hide axis across dendrograms except annotations and dendrogram
    fig['layout'][indices[icount-1]['yaxis_name']].update(showticklabels=True,ticks="outside",side='right')
    
    ### adjust chromosome labels
    for z in fig['layout']['annotations']:
        z['textangle']=-90
        
    ### Plot legends    
    for xanno in annotations+['cnv_clusters']:
        if annotations_category_colored[xanno]["type"]=="numeric":
            tmp=[round(x,3) for x in list(
                    np.arange(
                        annotations_category_colored[xanno]['cmin'],
                        annotations_category_colored[xanno]['cmax'],
                        (annotations_category_colored[xanno]['cmax']-annotations_category_colored[xanno]['cmin'])/20
                    ))
                ]+[round(annotations_category_colored[xanno]['cmax'],2)]

            fig.append_trace(
            go.Scattergl(
                x=tmp,
                y=[xanno]*len(tmp),
                marker=dict(symbol='square',
                            size=20,
                            color=tmp,
                            cmin=annotations_category_colored[xanno]['cmin'],
                            cmax=annotations_category_colored[xanno]['cmax'],
                            colorscale=annotations_category_colored[xanno]['color']
                       ),
                showlegend=False,
                mode='markers+text',
                text=[tmp[0]]+["" for x in range(0,len(tmp)-2)]+[tmp[-1]],
                textfont=dict(size=10,color="black"),
                textposition=['middle left']*(len(tmp)-1)+['middle right']
            ),indices[icount]['xaxis_num'],indices[icount]['yaxis_num']
            )
            fig['layout'][indices[icount]['yaxis_name']].update(
                showticklabels=True,
                showgrid=False,
                zeroline=False,
                title="",
                tickfont=dict(size=20)
            )
            fig['layout'][indices[icount]['xaxis_name']].update(
                showticklabels=False,
                showgrid=False,
                zeroline=False,                                                         
                range=[
                    annotations_category_colored[xanno]['cmin']-4.5*(annotations_category_colored[xanno]['cmax']-annotations_category_colored[xanno]['cmin'])/20,
                    annotations_category_colored[xanno]['cmax']+4.5*(annotations_category_colored[xanno]['cmax']-annotations_category_colored[xanno]['cmin'])/20
                ]
            ) 
        else:
            tmp=[annotations_category_colored[xanno][x]['num'] for x in list(annotations_category_colored[xanno].keys())[1:]]
            fig.append_trace(
            go.Scattergl(
                x=tmp,
                y=[xanno]*len(list(annotations_category_colored[xanno].keys())[1:]),
                marker=dict(symbol='square',size=20,color=[annotations_category_colored[xanno][z]['color'] for z in list(annotations_category_colored[xanno].keys())[1:]]
                       ),
                text=[z for z in list(annotations_category_colored[xanno].keys())[1:]],
                showlegend=False,
                mode='markers+text',
                textposition='middle left',
                textfont=dict(color='black',size=15)
            ),indices[icount]['xaxis_num'],indices[icount]['yaxis_num']
            )
            fig['layout'][indices[icount]['yaxis_name']].update(
                showticklabels=True,
                showgrid=False,
                zeroline=False,
                tickfont=dict(size=20)
            )
            fig['layout'][indices[icount]['xaxis_name']].update(
                showticklabels=False,
                showgrid=False,
                zeroline=False,
                range=[-0.3,1.1]
            )
        icount+=1;
        
    fig.append_trace(
        go.Scattergl(
            x=[50+x for x in range(int(cnv_min),int(cnv_max)+1,1)],
            text=[int(cnv_min)]+["" for x in range(int(cnv_min)+1,int(cnv_max),1)]+[int(cnv_max)],
            textposition=['middle left']*(len([x for x in range(int(cnv_min),int(cnv_max)+1,1)])-1)+['middle right'],
            y=['CNV' for x in range(int(cnv_min),int(cnv_max)+1,1)],
            mode="markers+text",
            marker=dict(symbol='square',size=20,color=[x for x in range(int(cnv_min),int(cnv_max)+1,1)],colorscale="RdBu"
                       ),
            textfont=dict(
            size=20,
            color="black"
            ),
            showlegend=False
        ),indices[icount]['xaxis_num'],indices[icount]['yaxis_num']
        )
    fig['layout'][indices[icount]['xaxis_name']]\
    .update(showticklabels=False,
            showgrid=False,
            zeroline=False,
            range=[40,125])
    fig['layout'][indices[icount]['yaxis_name']]\
    .update(showticklabels=True,
            showgrid=False,
            zeroline=False,
            title="",tickfont=dict(size=20))
    
    ### plotting 
    fig['layout'].update(width=1400,
                         height=1400,
                         margin={'t':150,'b':0},
                         paper_bgcolor='rgb(255,255,255)',
                         plot_bgcolor='rgb(255,255,255)'
                        )
    print(time.time()-t0)
    return(fig,cnv_clusters)
        
############################################################
def pool_pairwise_combination(cpgs,core_count,chr_list,difference_type,directory_path,libid):
    """
    Wrapper function for determining pairwise distance across single cell methylations samples
    Example : pool_pairwise_combination(["SampleA","SampleB"],4,["chr1","chr2","man_dist_scaled","/outdirectory/","JOB_NAME"])
    Saves intermediate files in results
    Returns Datafarme of pairwise distances
    """
    print("Running : Pooling Pairwisie combinations")
    t0 = time.time()
    combinations=list(itertools.combinations_with_replacement(cpgs,2))
    pool = mp.Pool(core_count,maxtasksperchild=1)
    chr_comb=[list(x)+[chr_list,difference_type] for x in combinations]
    chunks = [chr_comb[x:x+300] for x in range(0, len(chr_comb), 300)]
    chunks_to_run=[]
    for x in range(0,len(chunks)):
        if not(os.path.isfile(directory_path+"/results/"+libid+"_pr"+str(x)+".pkl")):
            chunks_to_run.append(chunks[0])
    
    if len(chunks_to_run)>0:
        for x in range(0,len(chunks_to_run)):
            pd.DataFrame(pool.map(pairwise_combination,chunks[x])).to_pickle(directory_path+"/results/"+libid+"_pr"+str(x)+".pkl") 
    
    results = pd.concat(pd.
                 read_pickle(sample) for sample in glob.iglob(
                     directory_path+"/results/"+libid+"_pr"+"*.pkl",
                     recursive=False
                 ))
    pool.close()
    del pool
    pairwise_other_half = results[[1,0,2]]
    pairwise_other_half.columns = [0,1,2]
    pairwise_df=pd.concat([results,pairwise_other_half])
    del results
    del pairwise_other_half
    pairwise_df= pairwise_df.rename(columns={0:'sample_1',1:'sample_2',2:difference_type})
    print(time.time()-t0)
    return pairwise_df

def pairwise_combination(samples):
    """
    Function for determining pairwise distance given SampleA,SampleB,chromosome list, and distance type
    Possible distance functions: 'pearson','euclid_dist','man_dist','man_dist_scaled','cityblock'
    Default : 'cityblock'
    Example : pariwise_combination(['SampleA','SampleB'])
    Returns SampleA,SampleB,distance
    """
    chr_list=samples[2]
    difference_type=samples[3]
    merged = (pd.merge(
            pd\
              .read_csv(samples[0], 
                  compression='gzip',
                  sep="\t",
                  header=None,
                  names=['chr','start','stop','A','B','C','meth'],
                  dtype={'chr':object,'start':int,'stop':float,'A':float,'B':float,'C':float,'meth':float})\
             .fillna(0.0)\
             .query('(meth==0 | meth==1) & chr in @chr_list')\
             .assign(CpG = lambda row :row['chr'].astype(str) +"_"+ row['start'].astype(str))\
             .drop(['chr','start','stop','A','B','C'],axis=1)\
             .set_index("CpG"),
            pd\
              .read_csv(samples[1], 
                  compression='gzip',
                  sep="\t",
                  header=None,
                  names=['chr','start','stop','A','B','C','meth'],
                  dtype={'chr':object,'start':int,'stop':float,'A':float,'B':float,'C':float,'meth':float})\
             .fillna(0.0)\
             .query('(meth==0 | meth==1) & chr in @chr_list')\
             .assign(CpG = lambda row :row['chr'].astype(str) +"_"+ row['start'].astype(str))\
             .drop(['chr','start','stop','A','B','C'],axis=1)\
             .set_index("CpG"),left_index=True,right_index=True,how='inner')
                 )
        
    if difference_type=='pearson':
        difference=1-pearsonr(merged["meth_x"].values.tolist(), merged["meth_y"].values.tolist())[0]
    elif difference_type=='euclid_dist':
        difference=distance.euclidean(merged["meth_x"].values.tolist(), merged["meth_y"].values.tolist())
    elif difference_type=='man_dist':
        difference = distance.cityblock(merged["meth_x"].values.tolist(), merged["meth_y"].values.tolist())
    elif difference_type=='man_dist_scaled':
        difference = distance.cityblock(merged["meth_x"].values.tolist(), merged["meth_y"].values.tolist())/merged.shape[0]
    elif difference_type=='euclid_dist_scaled':
        difference= (sum((merged["meth_x"]-merged["meth_y"])**2)/merged.shape[0])**0.5
    else :
          difference = distance.cityblock(merged["meth_x"].values.tolist(), merged["meth_y"].values.tolist())/merged.shape[0]
            
    del merged
    return (samples[0],samples[1],difference)
############################################################
def plotPairwise_heatmap(pairwise_array,stats,annotations,annotations_category_colored,cut_tree,difference_type):
    """
    Wrapper ploting heatmap and associated annotations for pairwise clustering of single cell samples
    Example : plotPairwise_heatmap(.pool_pairwise_combination()  Output,.pullStatistics() statistics Output,['Methylation',"Example Annotation"],.ready_annotations() output, Number Of Tree clusters,"man_dist_scaled")
    Returns figure instance and Single cell methylation groupings
    """
    print("Running : Generating Heatmap")
    t0 = time.time()
    ###Set dimensions given pw_array
    xdim = 12 if pairwise_array.shape[1] < 12 else pairwise_array.shape[1]
    ydim = 12 if pairwise_array.shape[0] < 12 else pairwise_array.shape[0]
    ### Convert pairwise distances to euclidean distances
    euclid_pairwise_array=pd.DataFrame()

    for row in pairwise_array.index.values.tolist():
        for col in pairwise_array.columns.values.tolist():
            row_array=pairwise_array.loc[:,row].values.tolist()
            col_array=pairwise_array.loc[:,col].values.tolist()
            dist=distance.euclidean(row_array,col_array)
            euclid_pairwise_array.loc[row,col]=distance.euclidean(pairwise_array.loc[:,row].values.tolist(), pairwise_array.loc[:,col].values.tolist())

    ### Assign Spacing
    specs=[]
    ###Space for horizontal dendrogram
    specs.append(
        [None]*(round(ydim/6)+len(annotations)+1)+\
        [{'rowspan': round(ydim/6),'colspan': xdim}]+\
        [None]*(ydim-1)
    )
    ### Filler space for horizontal dendogram
    for x in range(0,round(ydim/6)-1):
        specs.append([None]*len(specs[0]))
    ### Add space per horizontal annotation
    for x in range(0,len(annotations)+1):
        specs.append(
            [None]*(round(ydim/6)+len(annotations)+1)+\
            [{'rowspan': round(ydim/12),'colspan': xdim}]+\
            [None]*(ydim-1)
    )
    ### Make a temporary array cause I'm too much of an idiot how to figure out how to do it in one shot
    tmp_array=[]
    ### Space for vertical dendrogram
    tmp_array.append({'rowspan': ydim,'colspan': round(xdim/6)})
    ### Filler Vertical dendrogram spacing
    for x in range(0,round(ydim/6)-1):
        tmp_array.append(None)
    ### Add space per vertical annotation
    for x in range(0,len(annotations)+1):
        tmp_array.append({'rowspan': ydim,'colspan': round(xdim/12)})
    ### Add heatmap
    tmp_array.append({'rowspan': ydim,'colspan': xdim})
    ### Filler heatmap spacing : columns
    for x in range(0,xdim-1):
        tmp_array.append(None)
    ### Add to specs
    specs.append(tmp_array);del tmp_array
    ### Filler spacing : rows
    for x in range(0,xdim+1):
        specs.append([None]*len(specs[0]))
    ### Add space per annotation legend
    for x in range(0,len(annotations)+2):
        specs.append([None]*(len(specs[0])-ydim)+[{'rowspan': 2,'colspan': ydim}]+[None]*(ydim-1))
        specs.append([None]*len(specs[0]));specs.append([None]*len(specs[0]));specs.append([None]*len(specs[0]))
        
    specs.pop();specs.pop()


    ### Hierarchical clustering of euclidean array
    Z = hierarchy.linkage(euclid_pairwise_array, 'ward')
    ### Make into dendrograms
    dn = hierarchy.dendrogram(Z, labels = euclid_pairwise_array.columns,no_plot=True)
    ### Define clusters
    cutree = hierarchy.cut_tree(Z, n_clusters=cut_tree)
    pdclust_clusters=pd.DataFrame(index=euclid_pairwise_array.columns)
    pdclust_clusters['pdclust_clusters']=[chr(x[0]+65) for x in cutree]
    ### Set coordinates and orderlabels
    icoord = dn['icoord']
    dcoord = dn['dcoord']
    ordered_labels = dn['ivl']
            
    ### add clustering to annotations
    annotations_category_colored['pdclust_clusters']={}
    annotations_category_colored['pdclust_clusters']['type']='string'
    colorlist=annotations_category_colored['annotation_colors'].pop(0)
    unique=pdclust_clusters.loc[:,"pdclust_clusters"].unique().tolist()
    if len(unique)<=2:
        num_list=[0,1]
    else:
        num_list=list(np.arange(0,1,1/(len(unique)-1)))
        num_list.append(1)
    for yanno,ycolor,ynum in zip(unique,colorlist[:len(unique)],num_list):
            annotations_category_colored['pdclust_clusters'][yanno]={}
            annotations_category_colored['pdclust_clusters'][yanno]['num']=ynum
            annotations_category_colored['pdclust_clusters'][yanno]['color']=ycolor
    



    ###Retrieve leaves
    yvals_flat=[item for sublist in dcoord for item in sublist]
    xvals_flat =[item for sublist in icoord for item in sublist]
    zero_vals = []
    for i in range(len(yvals_flat)):
        if yvals_flat[i] == 0.0 and xvals_flat[i] not in zero_vals:
            zero_vals.append(xvals_flat[i])

    if len(zero_vals) > len(dcoord) + 1:
            l_border = int(min(zero_vals))
            r_border = int(max(zero_vals))
            correct_leaves_pos = range(l_border,
                                       r_border + 1,
                                       int((r_border - l_border) / len(dcoord)))
            # Regenerating the leaves pos from the self.zero_vals with equally intervals.
            zero_vals = [v for v in correct_leaves_pos]

    zero_vals.sort()

    ###initialize plotting frame
    fig = plotly.subplots.make_subplots(rows=len(specs),
                                  cols=len(specs[0]),
                                  print_grid=False,
                              vertical_spacing=0,
                              horizontal_spacing=0,
                              specs=specs,
                                 )
    
    icount=0
    indices=[]
    m=re.findall(r"\(.*?y[0-9]*",fig._grid_str.replace("(empty)",""))
    for x in m:
        tmp={}
        tmp['xaxis_num']=int(x.split(" ")[0].split(",")[0].replace("(",""))
        tmp['yaxis_num']=int(x.split(" ")[0].split(",")[-1].replace(")",""))
        tmp['xaxis_name']="xaxis"+x.split(" ")[1].split(",")[0][1:]
        tmp['yaxis_name']="yaxis"+x.split(" ")[1].split(",")[1][1:]
        indices.append(tmp)

    ###plot nodes to horizontal dendrogram
    for label,dcoor,icoor in zip(ordered_labels,dcoord,icoord):
            fig.append_trace(
                go.Scatter(
                    x=icoor,
                    y=dcoor,
                    mode='lines',
                    text=ordered_labels,
                    marker=dict(color='black'),
                    showlegend=False,
                    hoverinfo='none'
                ),indices[icount]['xaxis_num'],indices[icount]['yaxis_num']
            )

    ###set horizontal dendrogram labels
    fig.layout[indices[icount]['xaxis_name']].update(
        ticktext=ordered_labels,
        tickfont=dict(size=8),
        tickvals=zero_vals,
        showticklabels=False,
        ticks="",range=[min(zero_vals)-min(zero_vals),max(zero_vals)+min(zero_vals)])
    fig.layout[indices[icount]['yaxis_name']].update(gridcolor='lightgrey')
    icount+=1

    ### Plot horizontal annotations
    for xanno in annotations+["pdclust_clusters"]:
        if annotations_category_colored[xanno]["type"]=="numeric":
            tmp=stats.loc[ordered_labels,xanno]
            fig.append_trace(
            go.Heatmap(
                z=[[x for x in tmp.values.tolist()]],
                x=tmp.index.values.tolist(),
                zmin=annotations_category_colored[xanno]['cmin'],
                zmax=annotations_category_colored[xanno]['cmax'],
                colorscale=annotations_category_colored[xanno]['color'],showscale=False
            ),indices[icount]['xaxis_num'],indices[icount]['yaxis_num']
            )
        else:
            if xanno=='pdclust_clusters':
                tmp=pdclust_clusters.loc[ordered_labels,"pdclust_clusters"]
            else:
                tmp=stats.loc[ordered_labels,xanno]
            for yanno in [*annotations_category_colored[xanno].keys()][1:]:
                tmp=tmp.replace(yanno,annotations_category_colored[xanno][yanno]['num'])
                
            colorscale=[[annotations_category_colored[xanno][z]['num'],annotations_category_colored[xanno][z]['color']] for z in [*annotations_category_colored[xanno].keys()][1:]]
            if len(colorscale)==1:
                colorscale.insert(0,[0,colorscale[0][1]])

            fig.append_trace(
            go.Heatmap(
                z=[[x for x in tmp.values.tolist()]],
                x=tmp.index.values.tolist(),
                colorscale=colorscale,
                showscale=False
            ),indices[icount]['xaxis_num'],indices[icount]['yaxis_num']
            )   
        fig.layout[indices[icount]['yaxis_name']].update(showticklabels=False,ticks="")
        fig.layout[indices[icount]['xaxis_name']].update(showticklabels=False,ticks="")
        icount+=1 

    ###plot nodes to vertical dendrogram
    for label,dcoor,icoor in zip(ordered_labels,dcoord,icoord):
        fig.append_trace(
            go.Scatter(
                x=dcoor,
                y=icoor,
                mode='lines',
                #text=ordered_labels,
                marker=dict(color='black'),
                showlegend=False
            ),indices[icount]['xaxis_num'],indices[icount]['yaxis_num']
        )
    ### Set veritcal dendrogram labels
    fig.layout[indices[icount]['xaxis_name']].update(autorange='reversed',gridcolor='lightgrey')
    fig.layout[indices[icount]['yaxis_name']].update(
                                                 ticktext=ordered_labels,
                                                 tickfont=dict(size=8),
                                                 tickvals=zero_vals,showticklabels=False,ticks="",
                                                 range=[max(zero_vals)+min(zero_vals),min(zero_vals)-min(zero_vals)],
                                                 side='right')
    icount+=1
    ### Plot horizontal annotations
    for xanno in annotations+["pdclust_clusters"]:
        if annotations_category_colored[xanno]["type"]=="numeric":
            tmp=stats.loc[ordered_labels[::-1],xanno]
            fig.append_trace(
            go.Heatmap(
                z=[[x] for x in tmp.values.tolist()],
                y=tmp.index.values.tolist(),
                zmin=annotations_category_colored[xanno]['cmin'],
                zmax=annotations_category_colored[xanno]['cmax'],
                colorscale=annotations_category_colored[xanno]['color'],showscale=False
            ),indices[icount]['xaxis_num'],indices[icount]['yaxis_num']
            )
        else:
            if xanno=='pdclust_clusters':
                tmp=pdclust_clusters.loc[ordered_labels[::-1],"pdclust_clusters"]
            else:
                tmp=stats.loc[ordered_labels[::-1],xanno]
            for yanno in [*annotations_category_colored[xanno].keys()][1:]:
                tmp=tmp.replace(yanno,annotations_category_colored[xanno][yanno]['num'])

            colorscale=[[annotations_category_colored[xanno][z]['num'],annotations_category_colored[xanno][z]['color']] for z in [*annotations_category_colored[xanno].keys()][1:]]
            if len(colorscale)==1:
                colorscale.insert(0,[0,colorscale[0][1]])
            fig.append_trace(
            go.Heatmap(
                z=[[x] for x in tmp.values.tolist()],
                y=tmp.index.values.tolist(),
                colorscale=colorscale,
                showscale=False
            ),indices[icount]['xaxis_num'],indices[icount]['yaxis_num']
            )   
        fig.layout[indices[icount]['yaxis_name']].update(showticklabels=False,ticks="")
        fig.layout[indices[icount]['xaxis_name']].update(showticklabels=False,ticks="")
        icount+=1

    ###HEATMAP
    fig.append_trace(
            go.Heatmap(
                z=pairwise_array.loc[ordered_labels,ordered_labels[::-1]].values.tolist(),
                y=ordered_labels[::-1],x=ordered_labels,
                zmin=pairwise_array.min().min(),
                zmax=pairwise_array.max().max(),
                showscale=False,
                colorscale="RdBu"
            ),indices[icount]['xaxis_num'],indices[icount]['yaxis_num']
    )
    fig.layout[indices[icount]['yaxis_name']].update(side='right')
    fig.layout[indices[icount]['xaxis_name']].update(side='bottom',showticklabels=False,ticks="")
    icount+=1


    ### Plot annotation legends
    for xanno in annotations+['pdclust_clusters']:
        if annotations_category_colored[xanno]["type"]=="numeric":
            tmp=[round(x,3) for x in list(
                    np.arange(
                        annotations_category_colored[xanno]['cmin'],
                        annotations_category_colored[xanno]['cmax'],
                        (annotations_category_colored[xanno]['cmax']-annotations_category_colored[xanno]['cmin'])/20
                    ))
                ]+[round(annotations_category_colored[xanno]['cmax'],2)]

            fig.append_trace(
            go.Scattergl(
                x=tmp,
                y=[xanno]*len(tmp),
                marker=dict(symbol='square',size=20,color=tmp,cmin=annotations_category_colored[xanno]['cmin'],cmax=annotations_category_colored[xanno]['cmax'],colorscale=annotations_category_colored[xanno]['color']
                       ),
                showlegend=False,
                mode='markers+text',
                text=[tmp[0]]+["" for x in range(0,len(tmp)-3)]+[tmp[-1]],
                textfont=dict(size=10,color="black"),
                textposition=['middle left']+['middle left']*18+["middle right"],
            ),indices[icount]['xaxis_num'],indices[icount]['yaxis_num']
            )
            fig['layout'][indices[icount]['yaxis_name']].update(showticklabels=True,showgrid=False,zeroline=False,tickfont=dict(size=10))
            fig['layout'][indices[icount]['xaxis_name']].update(showticklabels=False,
                                                            showgrid=False,
                                                            zeroline=False,
                                                            range=[
                                                                annotations_category_colored[xanno]['cmin']-4.5*(annotations_category_colored[xanno]['cmax']-annotations_category_colored[xanno]['cmin'])/20,
                                                                annotations_category_colored[xanno]['cmax']+4.5*(annotations_category_colored[xanno]['cmax']-annotations_category_colored[xanno]['cmin'])/20
                                                                                                              ]
                                      ) 
        else:
            tmp=[annotations_category_colored[xanno][x]['num'] for x in list(annotations_category_colored[xanno].keys())[1:]]
            fig.append_trace(
            go.Scattergl(
                x=tmp,
                y=[xanno]*len(list(annotations_category_colored[xanno].keys())[1:]),
                marker=dict(symbol='square',size=20,color=[annotations_category_colored[xanno][z]['color'] for z in list(annotations_category_colored[xanno].keys())[1:]]
                       ),
                text=[z for z in list(annotations_category_colored[xanno].keys())[1:]],
                showlegend=False,
                mode='markers+text',
                textposition='middle left',
                textfont=dict(color='black',size=10)
            ),indices[icount]['xaxis_num'],indices[icount]['yaxis_num']
            )
            fig['layout'][indices[icount]['yaxis_name']].update(showticklabels=True,showgrid=False,zeroline=False,tickfont=dict(size=10))
            fig['layout'][indices[icount]['xaxis_name']].update(showticklabels=False,showgrid=False,zeroline=False,range=[-0.3,1.1])
        icount+=1

    ### Set pairwise array max/min and plot legend
    dist_min=float(pairwise_array.min().min())
    dist_max=float(pairwise_array.max().max())

    fig.append_trace(
        go.Scattergl(
            x=np.arange(dist_min,dist_max,((dist_max-dist_min)/20)),
            text=[int(dist_min)]+["" for x in np.arange(dist_min,dist_max,((dist_max-dist_min)/20))[:-2]]+[round(dist_max,2)],
            textposition=['middle left']+['middle left']*18+["middle right"],
            #z=[x for x in range(int(cnv_min),int(cnv_max)+1,1)],
            y=[difference_type]*20,
            mode="markers+text",
            marker=dict(symbol='square',size=20,color=np.arange(dist_min,dist_max,((dist_max-dist_min)/20)),colorscale="RdBu"
                       ),
            textfont=dict(
            size=10,
            color="black"
            ),
            showlegend=False,
        ),indices[icount]['xaxis_num'],indices[icount]['yaxis_num']
        )
    fig['layout'][indices[icount]['yaxis_name']].update(showticklabels=True,showgrid=False,zeroline=False,tickfont=dict(size=10))
    fig['layout'][indices[icount]['xaxis_name']].update(showticklabels=False,showgrid=False,zeroline=False,range=[dist_min-2*((dist_max-dist_min)/20),dist_max+((dist_max-dist_min)/20)])

    fig['layout'].update(width=1400,
                         height=1400,
                         margin={'t':50,'l':50,'r':50},
                         paper_bgcolor='rgb(255,255,255)',
                         plot_bgcolor='rgb(255,255,255)'
                        )
    print(time.time()-t0)
    return(fig,pdclust_clusters)

##################################################################
def plot_scatter_MDS(pairwise_array,stats,annotation,annotations_category_colored):
    """
    Function for plotting MDS on pairwise distances
    Example : plot_scatter_MDS(.pool_pairwise_combination()  Output,.pullStatistics() statistics Output,['Methylation',"Example Annotation"],.ready_annotations() output)
    Returns scatterplot figure instance with annotations 
    """
    print("Running : MDS scatter plot")
    t0 = time.time()
    embedding = MDS(n_components=2,dissimilarity='precomputed', random_state=1)
    X_transformed = embedding.fit_transform(pairwise_array)
    
    fig = plotly.subplots.make_subplots(rows=1,cols=1)
    if isinstance(stats.loc[:,annotation].values.tolist()[0],float):
        fig.append_trace(go
                         .Scattergl(
                             x=X_transformed[:,0],
                             y=X_transformed[:,1],
                             marker=dict(
                                 symbol='square',
                                 size=20,
                                 color=stats.loc[pairwise_array.columns.tolist(),annotation].values.tolist(),
                                 cmin=annotations_category_colored[annotation]['cmin'],
                                 cmax=annotations_category_colored[annotation]['cmax'],
                                 colorscale=annotations_category_colored[annotation]['color'],
                                 colorbar=dict(thickness=20)
                             ),
                             mode='markers',
                             )
                             ,1,1)
    else:
        for inst in stats.loc[pairwise_array.columns.tolist(),"pdclust_clusters"].unique().tolist():
            tmp=[i for i,val in enumerate(stats.loc[pairwise_array.columns.tolist(),annotation].values.tolist()) if val==inst]
            fig.append_trace(go
                             .Scattergl(
                                 x=X_transformed[tmp,0],
                                 y=X_transformed[tmp,1],
                                 name=inst,
                                 marker=dict(
                                     symbol='square',
                                     size=20,
                                     color=[annotations_category_colored[annotation][inst]['color']]*len(tmp)
                                     ),
                                 mode='markers',
                                 showlegend=True
                             ),1,1)  


    fig['layout']['xaxis1'].update(gridcolor='lightgrey',
                                   linecolor='lightgrey',
                                   zerolinecolor='lightgrey',
                                   zeroline=True,
                                   title="Dimension 1")
    fig['layout']['yaxis1'].update(gridcolor='lightgrey',
                                   linecolor='lightgrey',
                                   zerolinecolor='lightgrey',
                                   zeroline=True,
                                   title="Dimension 2")
    fig['layout'].update(width=800,
                         height=800,
                         title='MDS '+annotation,
                         paper_bgcolor='rgb(255,255,255)',
                         plot_bgcolor='rgb(255,255,255)'
                        )
    print(time.time()-t0)
    return(fig)
###############################################################
def plot_scatter_pca(pairwise_array,stats,annotation,annotations_category_colored):
    """
    Function for plotting PCA on pairwise distances
    Example : plot_scatter_pca(.pool_pairwise_combination()  Output,.pullStatistics() statistics Output,['Methylation',"Example Annotation"],.ready_annotations() output)
    Returns scatterplot figure instance with annotations 
    """
    print("Running : PCA scatter plot")
    t0 = time.time()
    pca = PCA(n_components=2)
    X_transformed=pca.fit_transform(pairwise_array)
    
    fig = plotly.subplots.make_subplots(rows=1,cols=1)
    if isinstance(stats.loc[:,annotation].values.tolist()[0],float):
        fig.append_trace(go
                         .Scattergl(
                             x=X_transformed[:,0],
                             y=X_transformed[:,1],
                             marker=dict(
                                 symbol='square',
                                 size=20,
                                 color=stats.loc[pairwise_array.columns.tolist(),annotation].values.tolist(),
                                 cmin=annotations_category_colored[annotation]['cmin'],
                                 cmax=annotations_category_colored[annotation]['cmax'],
                                 colorscale=annotations_category_colored[annotation]['color'],
                                 colorbar=dict(thickness=20)
                             ),
                             mode='markers',
                             )
                             ,1,1)
    else:
        for inst in stats.loc[pairwise_array.columns.tolist(),"pdclust_clusters"].unique().tolist():
            tmp=[i for i,val in enumerate(stats.loc[pairwise_array.columns.tolist(),annotation].values.tolist()) if val==inst]
            fig.append_trace(go
                             .Scattergl(
                                 x=X_transformed[tmp,0],
                                 y=X_transformed[tmp,1],
                                 name=inst,
                                 marker=dict(
                                     symbol='square',
                                     size=20,
                                     color=[annotations_category_colored[annotation][inst]['color']]*len(tmp)
                                     ),
                                 mode='markers',
                                 showlegend=True
                             ),1,1) 

    fig['layout']['xaxis1'].update(gridcolor='lightgrey',
                                   linecolor='lightgrey',
                                   zerolinecolor='lightgrey',
                                   zeroline=True,
                                   title="PCA1 ("+str(round(pca.explained_variance_ratio_[0],2))+")")
    fig['layout']['yaxis1'].update(gridcolor='lightgrey',
                                   linecolor='lightgrey',
                                   zerolinecolor='lightgrey',
                                   zeroline=True,
                                   title="PCA2 ("+str(round(pca.explained_variance_ratio_[0],2))+")")
    fig['layout'].update(width=800,
                         height=800,
                         title='PCA '+annotation,
                         paper_bgcolor='rgb(255,255,255)',
                         plot_bgcolor='rgb(255,255,255)',
                        )        
        
    print(time.time()-t0)
    return(fig)
#################################################################################################
def merge_cpgs(cpgs,core_count=4):
    """
    Wrapper merging CpG libraries into a smoothed library via Bsseq
    Requires Nested array of consisting of 1xN number of Groups, each group consisting of unique index, and Number of cores
    Example: merge_cpg([[A1,A2,A3],[B1,B2,B3],[C1,C2,C3]],4)
    Returns normalized CpG methylation dataframe
    """
    print("Running : Merging CpGs")
    t0 = time.time()
    bsseq = importr('bsseq')
    
    cpg_tracker=pd.DataFrame()
    cpg_dataframes=[]

    ###Name Groups
    for group in range(0,len(cpgs)):
        for sample in cpgs[group]:
            cpg_tracker.loc[sample.split("/")[-1].split(".")[0],"file"]=sample
            cpg_tracker.loc[sample.split("/")[-1].split(".")[0],"group"]=chr(group+65)

    ###Read in CpGs per group member
    for sample_name,file in zip(cpg_tracker.index.values.tolist(),cpg_tracker['file'].values.tolist()):
        cpg_dataframes.append(
            pd.read_csv(file,
                        names=['chr','start','end','meth_cov','unmeth_cov','cov','meth_frac'],
                        usecols=['chr','start','cov','meth_frac'],
                        compression='gzip',
                        sep='\t'
                       ).set_index(['chr','start']).rename(columns={"cov":sample_name+"_cov","meth_frac":sample_name+"_meth"})
        )

    ###Combine CpGs into single Dataframe
    merged_cpgs=reduce(lambda  left,right: pd.merge(left,right,left_index=True, right_index=True,how='outer'),cpg_dataframes)
    merged_cpgs.replace(np.NaN,0,inplace=True)
    del(cpg_dataframes)

    ###Convert each aspect of Python Dataframe into R data.table
    ro.r.assign(
        "meth",
        ro.conversion.py2rpy(
            merged_cpgs.loc[:,[x+"_meth" for x in cpg_tracker.index.values.tolist()]].reset_index(drop=True)
        )
    )
    ro.r.assign(
        "cov",
        ro.conversion.py2rpy(merged_cpgs.loc[:,[x+"_cov" for x in cpg_tracker.index.values.tolist()]].reset_index(drop=True)
                            )
    )
    ro.r.assign(
        "loc",ro.conversion.py2rpy(merged_cpgs.reset_index().loc[:,["chr","start"]]
                                  )
    )
    ro.r.assign(
        "names_list",
        ro.StrVector(cpg_tracker.index.values.tolist())
    )
    ro.r.assign(
        "group_list",
        ro.StrVector(cpg_tracker['group'].values.tolist())
    )
    
    ###Covert R data.table into BSseq object
    ro.r('bsseq_obj<-BSseq(M = as.matrix(meth), Cov = as.matrix(cov), chr = loc$chr, pos = loc$start, sampleNames =names_list)')
    ###Remove intermidates
    ro.r('rm(meth)')
    ro.r('rm(cov)')
    ro.r('rm(loc)')
    ###Collapse BSseq based on groups
    ro.r('combined<-collapseBSseq(bsseq_obj,group=group_list)')
    ro.r('rm(bsseq_obj)')
    ###Run smoothing function 
    ###Becareful with thread allocation. python is extremely odd in this scenario where the same variables/memory usage is applied per thread
    ro.r('smoothed_bsseq<-BSmooth(combined, h = 1000, verbose=TRUE, BPPARAM = MulticoreParam(workers = '+str(core_count)+'))')
    ro.r('rm(combined)')

    ###Convert BSseq into Python DataFrame
    smooth_python_df=pd.DataFrame()

    
    smooth_python_df['chr']=[x for x in  ro.r('as.character(seqnames(granges(smoothed_bsseq)))')]
    smooth_python_df['start']=[x for x in  ro.r('as.numeric(start(granges(smoothed_bsseq)))')]
    for group,num in zip(cpg_tracker['group'].unique().tolist(),range(0,len(cpg_tracker['group'].unique().tolist()))):
            smooth_python_df['meth_'+group]=[round(x[num],2) for x in ro.r('getMeth(smoothed_bsseq)')]
            smooth_python_df['cov_'+group]=[round(x[num],2) for x in ro.r('getCoverage(smoothed_bsseq)')]
    
    ro.r('rm(smoothed_bsseq)')
    print(time.time()-t0)
    return(smooth_python_df)
##########################################################
def find_DMRs(smoothed_python_df,min_cpg_cov,min_cpg_in_window,fdr_cutoff,cpg_window):
    """
    Wrapper for detecting DMRs between two bisulifite(merged) libraries
    Requires Smoothed_df from .merge_CpGs(), Minimum CpG coverage over merged, Minimum CpGs within Window, FDR cutoff, Window size
    Example : find_DMRs(Smoothed_DataFRame,3,3,0.01,200)
    Returns Differentially methylated CpGs, DMRs, and figures for intra DM-CpG distances,Dm-CpG distributin, and DMR sizes
    """
    print("Running : Scanning for DMRs")
    t0 = time.time()
    min_cpg_cov=3
    min_cpg_in_window=3
    fdr_cutoff=0.01
    cpg_window=200
    ### Take smoothed combined data, select for those with coverage >=3 CpGs and take difference
    difference=smoothed_python_df.dropna(how='any').query("cov_A>=@min_cpg_cov & cov_B>=@min_cpg_cov").assign(diff= lambda row : row['meth_A']-row['meth_B'])
    ### calculate zscore
    difference['z_score']=scipy.stats.zscore(difference['diff'].values.tolist(),axis=0)
    ### calculate pscore; see http://www.cyclismo.org/tutorial/R/pValues.html for rationale and set up
    difference['p_score']=scipy.stats.norm.cdf((-1*abs(difference['z_score'])).values.tolist())*2
    ### calulates FDR
    difference['fdr']=multitest.multipletests(difference["p_score"].values.tolist(),method='fdr_bh')[1]
    ### calculate upper and lower bound methylation differences
    diff_lower= difference.query("fdr<@fdr_cutoff&diff<0").loc[:,'diff'].nlargest(1).values[0]
    diff_upper= difference.query("fdr<@fdr_cutoff&diff>0").loc[:,'diff'].nsmallest(1).values[0]
    difference['meth_bin']=pd.cut(difference['diff']
        ,np.arange(difference["diff"].min(),difference["diff"].max(),(difference["diff"].max()-difference["diff"].min())/50))
    #####################
    difference['meth_bin']=pd.cut(difference['diff']
        ,np.arange(difference["diff"].min(),difference["diff"].max(),(difference["diff"].max()-difference["diff"].min())/50))
    
    ### Plot distribution of methylation difference
    fig_diff = plotly.subplots.make_subplots(rows=1,cols=1)
    tmp=difference.groupby("meth_bin").count()
    fig_diff.append_trace(go.Scatter(y=tmp['chr'].values.tolist(),x=tmp.index.values.astype(str).tolist(),showlegend=False
    ),1,1
    )
    fig_diff.append_trace(go.Scatter(y=[0,tmp['chr'].max()*1.1],
                                x=pd.cut([diff_lower,diff_lower],np.arange(difference["diff"].min(),difference["diff"].max(),(difference["diff"].max()-difference["diff"].min())/50)).astype(str),
                                showlegend=False,
                                mode='lines',
                                line=dict(color='black',dash='dash'),
                               ),1,1
    )
    fig_diff.append_trace(go.Scatter(y=[0,tmp['chr'].max()*1.1],
                                x=pd.cut([diff_upper,diff_upper],np.arange(difference["diff"].min(),difference["diff"].max(),(difference["diff"].max()-difference["diff"].min())/50)).astype(str),
                                showlegend=False,
                                mode='lines',
                                line=dict(color='black',dash='dash'),
                               ),1,1
    )
    fig_diff['layout']['xaxis1'].update(gridcolor='lightgrey',
                                        linecolor='lightgrey',
                                        zerolinecolor='lightgrey')
    fig_diff['layout']['yaxis1'].update(gridcolor='lightgrey',
                                        linecolor='lightgrey',
                                        zerolinecolor='lightgrey')
    fig_diff['layout'].update(width=800,
                              height=650,
                              margin={'b':150},
                              title="CpG Methylation difference distrbution<Br>Hyper:"+\
                              str(len(difference.query("diff>=@diff_upper")))+\
                              ";Hypo:"+\
                             str(len(difference.query("diff<=@diff_lower")))+\
                             ";Within boundaries:"+\
                             str(len(difference.query("diff<@diff_upper and diff>@diff_lower"))),
                             paper_bgcolor='rgb(255,255,255)',
                             plot_bgcolor='rgb(255,255,255)'
                        )
    difference['meth_bin']=None
    ###########################
    ### subset hypo/hyper CpGs
    dm_CpGs = difference.query("diff<=@diff_lower or diff>=@diff_upper")
    ### calculate difference from nearest CpG to another.
    dm_CpGs['distance']= dm_CpGs.groupby('chr')['start'].transform(pd.Series.diff).fillna(cpg_window+1)
    
    dm_CpGs['distance_bin']=pd.cut(dm_CpGs['distance'],list(range(0,1000,10))+[dm_CpGs['distance'].max()+1])
    
    ### Plot 
    fig_dist = plotly.subplots.make_subplots(rows=1,cols=1)
    tmp=dm_CpGs.groupby("distance_bin").count()
    fig_dist.append_trace(go.Scatter(y=tmp['chr'].values.tolist(),x=tmp.index.values.astype(str).tolist(),showlegend=False
    ),1,1
    )
    fig_dist.append_trace(go.Scatter(y=[0,tmp['chr'].max()*1.1],
                                x=pd.cut([cpg_window,cpg_window],list(range(0,1000,10))+[dm_CpGs['distance'].max()+1]).astype(str),
                                showlegend=False,
                                mode='lines',
                                line=dict(color='black',dash='dash')
                               ),1,1
                    )
    fig_dist['layout']['xaxis1'].update(gridcolor='lightgrey',
                                        linecolor='lightgrey',
                                        zerolinecolor='lightgrey')
    fig_dist['layout']['yaxis1'].update(gridcolor='lightgrey',
                                        linecolor='lightgrey',
                                        zerolinecolor='lightgrey')
    
    fig_dist['layout'].update(width=800,
                             height=650,
                             margin={'b':150},
                             title="Significant CpG distances",
                             paper_bgcolor='rgb(255,255,255)',
                             plot_bgcolor='rgb(255,255,255)'
                        )

    ###########################
    ### Bin CpG. New bin if cpg distance is outside of window or methylation state changes 
    dm_CpGs['distance_bin']=None
    dm_CpGs = dm_CpGs.assign(bin=np.cumsum((dm_CpGs.loc[:,"distance"]>cpg_window)|(dm_CpGs.loc[:,"diff"]*dm_CpGs.loc[:,"diff"].shift(1).fillna(0.0)<0)))
    ### Calculate DMR start,stop, # of CpGs, mean methylation and total coverage across
    DMRs=dm_CpGs.groupby(["chr","bin"]).agg({'start': ['min', 'max','count'], 'meth_A': 'mean', 'meth_B': 'mean', 'cov_A': 'sum', 'cov_B': 'sum'})
    DMRs.columns = ["_".join(x) for x in DMRs.columns.ravel()]
    ### Output DMRs
    filtered_dmrs = (DMRs.query('start_count>=@min_cpg_in_window')\
                     .assign(start_max = lambda row : row['start_max']+1)\
                     .rename(columns={"start_min":"dmr_start",
                                     "start_max":"dmr_end",
                                    "start_count":"cpg_count"
                                    }
                           )
                    )
    ###########################
    fig_dmr = plotly.subplots.make_subplots(rows=1,cols=1)
    filtered_dmrs['size']=filtered_dmrs['dmr_end']-filtered_dmrs['dmr_start']
    fig_dmr.append_trace(go.Scatter(y=filtered_dmrs.sort_values('size')['size'].values.tolist(),
                                x=list(range(0,len(filtered_dmrs))),
                                mode='markers',
                                marker=dict(color=filtered_dmrs.sort_values('size')['cpg_count'].values.tolist(),colorbar=dict(thickness=20,title='# of CpGs')
                                           )
    ),1,1
    )

    fig_dmr['layout']['yaxis1'].update(title="DMR size (BP)",
                                       gridcolor='lightgrey',
                                       linecolor='lightgrey',
                                       zerolinecolor='lightgrey')
    fig_dmr['layout']['xaxis1'].update(showticklabels=True,
                                       gridcolor='lightgrey',
                                       linecolor='lightgrey',
                                       zerolinecolor='lightgrey')
    fig_dmr['layout'].update(width=800,
                             height=650,
                             margin={'b':150},
                             title="DMR size and # of Sig.CpGs",
                             paper_bgcolor='rgb(255,255,255)',
                             plot_bgcolor='rgb(255,255,255)'
                             #paper_bgcolor='rgb(255,255,255)',
                             #plot_bgcolor='rgb(255,255,255)'
                        )
    del tmp,difference
    print(time.time()-t0)
    return(dm_CpGs,filtered_dmrs,fig_diff,fig_dist,fig_dmr)
#############################################################
def ready_annotations(stats,annotations):
    """
    Function generates dictionary for annotations
    Example : .ready_annotations(.pullStatistics() output,['example_annotationsA','example_annotationsB'])
    Returns dictionary based on annotation definitions 
    """
    print("Running : Generating dictionary of annotations")
    t0 = time.time()
    annotations_category_colored={}
    annotations_category_colored['annotation_colors']=[
        ["#7FC97F","#BEAED4","#FDC086","#FFFF99","#386CB0","#F0027F","#BF5B17","#666666"],###Accent
        ["#8DD3C7","#FFFFB3","#BEBADA","#FB8072","#80B1D3","#FDB462","#B3DE69","#FCCDE5","#D9D9D9","#BC80BD","#CCEBC5","#FFED6F"],###Set3
        ["#66C2A5","#FC8D62","#8DA0CB","#E78AC3","#A6D854","#FFD92F","#E5C494","#B3B3B3"],###Set2
        ["#E41A1C","#377EB8","#4DAF4A","#984EA3","#FF7F00","#FFFF33","#A65628","#F781BF","#999999"],###Set1
        ["#B3E2CD","#FDCDAC","#CBD5E8","#F4CAE4","#E6F5C9","#FFF2AE","#F1E2CC","#CCCCCC"],###Pastel2
        ["#FBB4AE","#B3CDE3","#CCEBC5","#DECBE4","#FED9A6","#FFFFCC","#E5D8BD","#FDDAEC","#F2F2F2"],###Pastel1
        ["#1B9E77","#D95F02","#7570B3","#E7298A","#66A61E","#E6AB02","#A6761D","#666666"],###Dark2
    ]

    annotations_category_colored['colorscales']=[
        "Viridis",
        "Blackbody",
        "Bluered",
        "Blues",
        "Earth",
        "Electric",
        "Greens",
        "Greys",
        "Hot",
        "Jet",
        "Picnic",
        "Portland",
        "Rainbow",
        "RdBu",
        "Reds",
        "YlGnBu",
        "YlOrRd"]
    for xanno in annotations:
        annotations_category_colored[xanno]={}
        if isinstance(stats.loc[:,xanno].values.tolist()[0],str):
            annotations_category_colored[xanno]['type']='string'
            colorlist=annotations_category_colored['annotation_colors'].pop(0)
            unique=stats.loc[:,xanno].unique().tolist()
            if len(unique)==1:
                num_list=[1]
            elif len(unique)==2:
                num_list=[0,1]
            else:
                num_list=list(np.arange(0,1,1/(len(unique)-1)))
                num_list.append(1)
            for yanno,ycolor,ynum in zip(unique,colorlist[:len(unique)],num_list):
                    annotations_category_colored[xanno][yanno]={}
                    annotations_category_colored[xanno][yanno]['num']=ynum
                    annotations_category_colored[xanno][yanno]['color']=ycolor
        else:
            annotations_category_colored[xanno]['type']='numeric'
            annotations_category_colored[xanno]['color']=annotations_category_colored['colorscales'].pop(0)
            annotations_category_colored[xanno]['cmin']=stats.loc[:,xanno].min()
            annotations_category_colored[xanno]['cmax']=stats.loc[:,xanno].max()
            
    print(time.time()-t0)
    return(annotations_category_colored)
##############################################################################
