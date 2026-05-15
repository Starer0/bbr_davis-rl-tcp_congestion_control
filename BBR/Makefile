# Makefile for TCP BBR congestion control module

# 模块名称
obj-m += bbr_davis.o

# 源文件
tcp_bbr-objs := bbr_davis.o

# 内核源码目录
KERNELDIR ?= /lib/modules/$(shell uname -r)/build

# 当前目录
PWD := $(shell pwd)

# 构建规则
all:
	$(MAKE) -C $(KERNELDIR) M=$(PWD) modules

# 清理规则
clean:
	$(MAKE) -C $(KERNELDIR) M=$(PWD) clean

# 安装规则
install:
	$(MAKE) -C $(KERNELDIR) M=$(PWD) modules_install
	depmod -a

# 卸载规则
uninstall:
	rmmod bbr_davis
	rm -f /lib/modules/$(shell uname -r)/extra/bbr_davis.ko
	depmod -a
