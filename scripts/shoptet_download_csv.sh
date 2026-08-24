#!/usr/bin/env bash
set -e

mkdir -p data
curl -sL "https://www.mamito.cz/export/products.csv?patternId=27&partnerId=33&hash=62461a573739a6838662d347f4e4beca92c3bc12ebb221046b60a046fd379fd5" | iconv -f WINDOWS-1250 -t UTF-8 > data/shoptet.csv
echo "Ulozeno do data/shoptet.csv (UTF-8)"
