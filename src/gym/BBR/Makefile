obj-m := bbt.o
IDIR= /lib/modules/$(shell uname -r)/kernel/net/ipv4/
KDIR := /lib/modules/$(shell uname -r)/build
PWD := $(shell pwd)

default:
	$(MAKE) -C $(KDIR) SUBDIRS=$(PWD) 


clean:
	$(MAKE) -C $(KDIR) SUBDIRS=$(PWD) clean
