#!/usr/bin/env python3

# Only Support Linux

# 对比新打的包和旧包的差异
'''
packutil list <package>
                --diff [package2 列出并标注变化git 格式输出]
                --merge-diff [package1 [package2 [...]]] 处理软件包合并diff 假定 package 1合并了package2 [...]
packutil diff <package1> [package2 默认解析系统已安装同名包，也可以指定系统内已安装的其他包]
                --merge-diff [package1 [package2 [...]]]

                --system <package_name> #标记为系统包名

[ ] 支持将系统包传入 orig 和 sel 
[?] 一组包diff另一组包
[OK] 分层次的help

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
from pathlib import Path
import subprocess
from dataclasses import dataclass

PACKAGE_PASS_FILES = ['.BUILDINFO','.MTREE','.PKGINFO','']
PACMAN_PASS_FILES = ['']

@dataclass
class PkgModifiedStatus:
    ADDED = "add"
    REMOVED = "remove"

# TODO: Debian Package Support 
def get_pkg_file_list(pkgs_path: Path) -> list[str]:
    
    # 检查zstd
    zstd_check_cmd = subprocess.run([f"/usr/bin/file {pkgs_path} | /usr/bin/grep 'Zstandard compressed data' "] ,
                                    shell=True,
                                    capture_output=False,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL
                                    )
    if (zstd_check_cmd.returncode != 0 ): 
        raise Exception(f"${pkgs_path} 不是zstd压缩包")
    #剔除忽略项，生成表格
    files_process = subprocess.run(f"/usr/bin/bsdtar -tf {pkgs_path}",capture_output=True,shell=True) #列出文件
    files_list =[]
    for i in files_process.stdout.decode("UTF-8").split("\n") :  
        if i in PACKAGE_PASS_FILES :
            continue
        files_list.append(r"/"+i) #将路径补全成完整的系统路径

    return files_list

def get_pacman_package_file_list(pkg_name:str) -> list[str] : 
    package_process = subprocess.run(["/usr/bin/pacman","-Ql", f"{pkg_name}"],capture_output=True,text=True)
    if package_process.returncode != 0 :
        raise FileExistsError(f"没有从pacman中找到软件包${pkg_name}")

    pacman_package_file_list = []
    for i in  package_process.stdout.split("\n") :
        if i in PACMAN_PASS_FILES:
            continue
        pacman_package_file_list.append( i.split(' ',maxsplit=1)[1] )    

    return pacman_package_file_list

def package_files_compare(sel_pkg_files:list , **orig_pkg_files:list) -> list[dict] :
    '''  return [{status: "add|remove", file: "/usr/bin/foo"}]   '''
    orig_pkg_files_all = []
    for i in orig_pkg_files :
        orig_pkg_files_all += orig_pkg_files[i]

    added_files = set(sel_pkg_files) - set(orig_pkg_files_all)
    removed_files = set(orig_pkg_files_all) - set(sel_pkg_files)

    compare_result = []
    for i in added_files :
        compare_result.append({"status":PkgModifiedStatus.ADDED,"file":i})
    for i in removed_files :
        compare_result.append({"status":PkgModifiedStatus.REMOVED,"file":i})
    return compare_result

def print_diff(diff_result:list[dict]) -> None :
    for i in diff_result :
        if i["status"] == PkgModifiedStatus.ADDED :
            print(f"+ {i['file']}")
        elif i["status"] == PkgModifiedStatus.REMOVED :
            print(f"- {i['file']}")

# 哨兵值: "--diff 出现但没跟值" 时, 表示 "对比系统内已安装的同名包"
# 必须是一个用户敲不出来的字符串, 避免和真实包名混淆
SYSTEM = "__system__"

# ---------- type= 校验器: 坏参数在解析阶段就被拦下, 不用等业务逻辑 ----------
def package_file(value: str) -> str:
    """type= 接收一个函数: 输入命令行字符串, 校验后返回转换结果"""
    if not os.path.isfile(value):
        raise argparse.ArgumentTypeError(f"no such package file: {value!r}")
    return value
# -------

class Package():
    name = ""
    _files =[]
    
    def __init__(self, packageDescribe: list[str]| Path | str  , pkg_type: str = "pacman",
                 system_package: bool = False, ) -> None:
        """packageDescribe 能描述软件包文件的软件列表"""
        
        if isinstance(packageDescribe,list) :
            self._files = packageDescribe
        elif isinstance(packageDescribe,Path) :
            self._files = get_pkg_file_list(packageDescribe)
        elif ( packageDescribe ) and ( system_package == True ) :
            if pkg_type == "pacman":
                self._files = get_pacman_package_file_list(packageDescribe)  # pyright: ignore[reportArgumentType]
            else:
                raise NotImplementedError("暂不支持的包管理器")
        else:
            raise ValueError("传入参数不是合规的软件包描述")

    def get_files(self) -> list:
        return self._files

    @staticmethod
    def get_pacman_pkg_name(pkg_path:Path)->str:
        subprocess.run ( ["/usr/bin/mkdir","-p","/tmp/XLNC_PKG"] ).check_returncode()
        unzip = subprocess.run ( ["/usr/bin/tar","--zstd","-C","/tmp/XLNC_PKG/" ,"-xf" ,pkg_path.__str__(),".PKGINFO"] ,shell=False,capture_output=True)
        print(unzip.stdout.decode("UTF-8"))
        print(unzip.stderr.decode("UTF-8"))
        with open("/tmp/XLNC_PKG/.PKGINFO","r") as f:
            for line in f.readlines() :
                if line.startswith("pkgname") :
                    return line.split("=")[1].strip()
        raise ValueError(f"无法从 {pkg_path} 中解析出软件包名")
        return ""


def _D_list(package: Path)-> None :
    # 直接列出包内文件
    for i in get_pkg_file_list(package) :
        print(i)

def _D_diff_single(package1: Package, package2: Package) -> None :
    # 列出两个包的文件差异
    diff = package_files_compare(package1.get_files(), orig_pkg_files=package2.get_files())
    if not diff :
        print("软件包文件列表校验一致")
        return 
    print_diff(diff)

# ---------- 两个子命令的处理函数 (骨架, 等你填业务逻辑) ----------
def cmd_list(args: argparse.Namespace) -> int:
    sel_pkg_abs_path = [ Path(i).absolute() for i in args.packages ] #每个传入包的绝对路径
    diff_pkg_abs_path = [ Path(i).absolute() for i in args.diff ]

    print(f"packages   = {args.packages}")
    print(f"diff       = {args.diff!r}")        # None / SYSTEM / "用户给的包"
    print(f"merge_diff = {args.merge_diff!r}")  # None / [] / ["a", ...]
    print("-" * 20)

    if args.diff or args.merge_diff:
        # 解析 diff 对象: None / SYSTEM / "用户给的包"
        if args.diff == "__system__":
            orig_pkg = Package(sel_pkg_abs_path[0])
            diff_pkg = Package(Package.get_pacman_pkg_name(sel_pkg_abs_path[0]),system_package=True)
            _D_diff_single(orig_pkg,diff_pkg)
            return 0
            pass
        elif args.diff is not None:
            # 对比用户给的包
            pass
        elif args.merge_diff is not None:
            # 对比用户给的多个包
            pass

    if len(args.packages) != 1:
        raise NotImplementedError("暂不支持一次列出多个包的内容")
    _D_list(sel_pkg_abs_path[0]) 

    # 到这里参数已经全部解析好, 只管用 args.xxx
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    print(f"package1   = {args.package1!r}")
    print(f"package2   = {args.package2!r}")    # None → 业务逻辑里去解析系统已安装包
    print(f"merge_diff = {args.merge_diff!r}")
    return 0


# ---------- 构造解析器 ----------
def build_parser() -> argparse.ArgumentParser:
    # 第一层: packutil 本身
    parser = argparse.ArgumentParser(
        prog="packutil",
        description="A utility to list and diff package contents.",
        usage="packutil <action> [options]"
    )

    # 第二层: 子命令。dest="action" 让解析结果可用 args.action 读到 'list'/'diff';
    # required=True 表示 "packutil 光杆一个" 直接报错 (Python 3.7+)
    sub = parser.add_subparsers(
        dest="action",
        metavar="<action>",
        required=True,
    )

    # ============ 子命令: packutil list <package> [--diff [p2] | --merge-diff [p...]] ============
    p_list = sub.add_parser(
        "list",
        help="列出包内文件",
        description="列出包内文件, 可选用 --diff / --merge-diff 标注差异",
    )
    p_list.add_argument(
        "packages",
        nargs="+",                 # 位置参数: 至少一个包文件, 得到 list[str]
        metavar="<package>",
        type=package_file,         # 每个值都过一遍校验
        help="要列出的包文件",
    )
    # --diff 和 --merge-diff 是两种互斥的 diff 模式, 放进互斥组
    mode = p_list.add_mutually_exclusive_group()
    mode.add_argument(
        "--diff",
        nargs="?",                 # 值可给可不给 → "[package2]"
        const=SYSTEM,              # 出现但没给值 → 存成哨兵 SYSTEM
        default=None,              # 完全没出现 → None
        metavar="[package2]",
        help="以 git diff 格式标注变化; 不给值则对比系统内已安装的同名包",
    )
    mode.add_argument(
        "--merge-diff",
        nargs="*",                 # 0 个或多个值 → "[package ...]"
        default=None,              # None=没出现, []=出现了但没跟包名, [...] = 跟了包名
        metavar="[package ...]",
        help="假定 package 合并了这些包, 输出合并 diff",
    )
    # set_defaults(func=...): parse 之后 args.func 就是 cmd_list, main 里直接调用
    p_list.set_defaults(func=cmd_list)

    # ============ 子命令: packutil diff <package1> [package2] [--merge-diff [p...]] ============
    p_diff = sub.add_parser(
        "diff",
        help="对比两个包的差异",
        description="对比两个包; package2 缺省时解析系统内已安装的同名包",
    )
    p_diff.add_argument(
        "package1",
        metavar="<package1>",
        type=package_file,
        help="要检查的包文件",
    )
    p_diff.add_argument(
        "package2",
        nargs="?",                 # 可选位置参数: 不给则默认对比系统内已安装包
        default=None,
        metavar="[package2]",
        help="对比对象; 可以是包文件或系统内已安装的包名 (默认: 同名已安装包)",
    )
    p_diff.add_argument(
        "--merge-diff",
        nargs="*",
        default=None,
        metavar="[package ...]",
        help="假定 package1 合并了这些包, 输出合并 diff",
    )
    p_diff.set_defaults(func=cmd_diff)

    return parser


def main(argv: list[str] | None = None) -> int:
    # argv=None 时 argparse 自动读 sys.argv[1:];
    # 传参进来 (main(["list", "foo.pkg"])) 就能在测试/别的模块里直接调用
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)          # 派发给对应子命令的处理函数

if __name__ == "__main__":
    raise SystemExit(main())        # main 的返回值 → 进程退出码
