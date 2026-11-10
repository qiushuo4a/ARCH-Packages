# ARCH-Packages
Provide PKGBUILD files for many softwares 秋硕的PKGBUILD存储库
存储秋硕会用到的软件，由于秋硕通常比较懒，不会主动去查软件的更新情况，所以经常会出现软件落后的情况，需要新版本的时候可以参照HOW TO USE 节更新版本 或者提 ISSUE 受到issue 我应该很快就会更新版本


## HOW TO USE  用法说明
1下载整个存储库或者下载自己需要的包的打包脚本

a. 下载整个存储库 
```bash
git clone https://github.com/qiushuo4a/ARCH-Packages --depth 1
```
b.下载自己需要的打包脚本 
```txt
从github仓库页面进入需要的软件包文件夹 点开PKGBUILD 点击下载按钮 将文件保存到一个空白的文件夹
```

2 修改PKGBUILD（按需，在PKGBUILD不是最新版的脚本的情况下）
  ```bash
pkgver=0.4.6 #找到这一行 将pkgver改成github release最新版的版本号
pkgrel=2 #在修改上面的版本号之后 pkgrel 应重置为1,同版本每次修改+1

package(){ #打包函数 按需修改

}
  ```

3 构建软件包
```bash 
makepkg -Sicf #干净的完成打包并安装
# 或者
makepkg -f # 构建并覆盖上次的构建
pacman -U <生成的软件包文件>
```