#!/usr/bin/env python3

# Only Support Linux 

# 对比新打的包和旧包的差异
'''
packutil list <package> 
                --diff [package2 列出并标注变化git 格式输出]
                --merge-diff [package1 [package2 [...]]] 处理软件包合并diff 假定 package 1合并了package2 [...]
packutil diff <package1> [package2 默认解析系统已安装同名包，也可以指定系统内已安装的其他包]
                --merge-diff [package1 [package2 [...]]]

> packutil list foo-1.0-1-x86_64.pkg.tar.zst
/usr/bin/foo
/usr/lib/foo.so

> packutil diff foo-1.0-1-x86_64.pkg.tar.zst
[return code 0]

> packutil list foo-1.0-1-x86_64.pkg.tar.zst --diff bar
- /usr/bin/foo
- /usr/lib/foo.so
+ /usr/bin/bar
'''

import argparse
import os 

def build_parser():
    parser = argparse.ArgumentParser(description='Compare two packages')
    parser.add_argument('package1', help='Target package to compare')
    parser.add_argument('package2', help='Reference package to compare',required=False)

    return parser



parser = build_parser()

def main() -> int :
    return 0

if __name__ == "__main__":
    main()