#! /bin/python3

# Warning: This sripts is running on an unstable interface 

import os 
import subprocess
import re


def check_version_interface() -> str |None :
    #检查系统和依赖
    if os.uname().sysname != "Linux":
        print("This script is only supported on Linux.")
        raise NotImplementedError("Not supported OS")

    which_cffi =  subprocess.run(["which", "curl-cffi"], check=True)
    if which_cffi.returncode != 0:
        print("No curl-cffi find ")
        return None

    return process_webpage()

#curl-cffi get https://laplace.live/persona 
def process_webpage() -> str|None :
    webpage_text = subprocess.run(["curl-cffi","get","https://laplace.live/persona"], check=True, capture_output=True).stdout.decode("utf-8")

    if webpage_text == None :
        return None

    # 拆解字符串
    txt_ptr = webpage_text.find(".deb") 
    while txt_ptr != -1 :
        url_area = webpage_text[txt_ptr - 100 :txt_ptr + 20]
        last_quote_ptr = url_area.find('"', 100 )
        first_quote_ptr = url_area.rfind('"', 0 ,100 )
        url_text = url_area[first_quote_ptr : last_quote_ptr]
    #版本号匹配
        pattern = r'/v(\d+\.\d+\.\d+)/'
        version = re.search(pattern, url_text)

        if version != None :
            return version.group(1)

        txt_ptr += 5 #进5字符位置继续查找

    return None




if __name__ == "__main__":
    #检查系统和依赖
    if os.uname().sysname != "Linux":
        print("This script is only supported on Linux.")
        exit(1)
    which_cffi =  subprocess.run(["which", "curl-cffi"], check=True)
    if which_cffi.returncode != 0:
        print("No curl-cffi find ")
        exit(1)

    ver = process_webpage()

    if ver != None : 
        print ("laplace-persona 的版本:")
        print (ver)

    exit(1)
    













