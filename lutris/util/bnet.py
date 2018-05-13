import os
import re

from lutris.util.log import logger

regex_product_db = re.compile(b'\x0A..\x0A')


def products_parse(file):
    buf = file.read()

    products = {}

    for match_obj in regex_product_db.finditer(buf):
        offset = match_obj.start()
        offset += 4
        length = buf[offset]
        offset += 1
        name = buf[offset:offset+length].decode()
        offset += length
        offset += 1
        length = buf[offset]
        offset += 1
        code = buf[offset:offset+length].decode()
        offset += length
        offset += 3
        length = buf[offset]
        offset += 1
        path = buf[offset:offset+length].decode()
        offset += length
        products[name] = {
            'gameid': name,
            'path': path,
            'code': code
        }
    return products


def read_config(product_db_path):
    if not os.path.exists(product_db_path):
        return
    with open(product_db_path, "rb") as product_db_file:
        config = products_parse(product_db_file)
    try:
        config['battle.net']
    except KeyError as e:
        logger.error("Battle.net config %s is empty: %s", product_db_path, e)
        return
    else:
        return config
