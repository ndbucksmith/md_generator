import os
import math
import numpy as np
from collections import Counter
import pdb
"""    library for generating mark down tables from 2D arrays

"""

_print_out =  bool(os.getenv('PRINT_OUT', False))

def mdTable_str(str2d):
    line_lens = []
    lines_out = []
    tablestr = ''
    for i_ in range(len(str2d)):
        ll = 0
        for j_ in range(len(str2d[i_])):
            ll += len(str2d[i_][j_])
        line_lens.append(ll) 
    for i_ in range(len(str2d)):
        pstr = ''
        for j_ in range(len(str2d[i_])):
            pstr = pstr +  str2d[i_][j_] + '|'
        if _print_out:  print(pstr[0:-1])
        tablestr += pstr[0:-1] + '\n';  lines_out.append(pstr)
        if i_ == 0:
            pstr = ''
            for k_ in range(len(str2d[i_])):
                pstr=  pstr + ''.join('-' * len(str2d[i_][k_])) 
            pstr = pstr + '|'
            if _print_out:   print(pstr[0:-1])
            tablestr += pstr[0:-1] + '\n'; lines_out.append(pstr)         
    return tablestr, lines_out
