from ctypes import *
import time
import numpy as np

testlib1 = CDLL('./runer.so')
beta = 100.0

beta_100 = int(beta)
umsg = str(beta_100)
IN = c_int*10
data = IN()
count = 0
rcount = 0
bw = 0
t = 0
prtt = 0
while True:
	testlib1.run(umsg.encode('ascii'),data)
	fo=open("test.txt","a")
	fo.write("%d\n"%(data[0]))
	fo.close()
