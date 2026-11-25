#!/bin/bash

#Build files
rm -rv ./*/src/ 
rm -rv ./*/pkg/ 

#Package files
rm -rv ./*/*.deb 
rm -rv ./*/*.tar.* 
rm -rv ./*/*.part 

#Script Chache 
rm -rv ./*/__pycache__/


