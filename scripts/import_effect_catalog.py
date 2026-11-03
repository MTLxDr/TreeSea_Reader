"""Atomically replace derived effect tables after validating all source links."""
import argparse
import hashlib
import json
from pathlib import Path

import database


def import_catalog(db,path):
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    expected=data.pop('content_sha256')
    canonical=json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    if hashlib.sha256(canonical).hexdigest()!=expected:raise ValueError('Effect catalog checksum mismatch')
    if not data['summary']['source_coverage_complete']:raise ValueError('Source inventory coverage is incomplete')
    source_ids={}
    for item in data['entries']:
        source=item['source'];key=(source['provider'],source['url'],source['sha256'])
        if key not in source_ids:
            row=db.execute('SELECT id FROM documents WHERE provider=? AND source_url=? AND sha256=?',key).fetchone()
            if row is None:raise ValueError('Missing effect source: '+source['url'])
            source_ids[key]=row[0]
    db.execute('DELETE FROM effect_modifiers')
    db.execute('DELETE FROM effect_entries')
    for item in data['entries']:
        s=item['source'];doc=source_ids[(s['provider'],s['url'],s['sha256'])]
        item['source']['document_id']=doc
        db.execute('INSERT INTO effect_entries VALUES (?,?,?,?,?,?,?,?,?,?,?)',(
            item['id'],doc,item['category'],item['subject_id'],item['name'],item['variant'],item['side'],
            int(item['has_numeric']),item['review_status'],item['execution_status'],database.dumps(item)))
        for index,modifier in enumerate(item['modifiers']):
            db.execute('INSERT INTO effect_modifiers VALUES (?,?,?,?,?,?,?)',(
                item['id'],index,modifier['stat'],modifier['bucket'],modifier['value'],modifier['evidence'],database.dumps(modifier)))
    db.execute('INSERT OR REPLACE INTO database_meta VALUES (?,?)',('effect_catalog_hash',expected))
    db.execute('INSERT OR REPLACE INTO database_meta VALUES (?,?)',('effect_catalog_summary',database.dumps(data['summary'])))
    return {'entries':len(data['entries']),'modifiers':db.execute('SELECT COUNT(*) FROM effect_modifiers').fetchone()[0],'content_sha256':expected}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',type=Path,default=database.ROOT/'data/effects/catalog.json')
    args=parser.parse_args()
    with database.connect(database.DEFAULT_DB) as db:result=import_catalog(db,args.catalog)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
