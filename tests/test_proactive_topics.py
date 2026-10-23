import json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta
from email.utils import format_datetime
from proactive_topics import PublicTopics,parse_feed,current_sources,FEEDS,ZONE,MAX_BYTES

class TopicTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.now=datetime(2026,10,23,10,tzinfo=ZONE).timestamp();self.feed=FEEDS[0]
    def tearDown(self):self.temp.cleanup()
    def raw(self,at=None,url='https://www.chinanews.com.cn/test.shtml',title='一条当日资讯'):
        date=format_datetime(datetime.fromtimestamp(self.now-60 if at is None else at,ZONE))
        return f'<rss><channel><item><title>{title}</title><link>{url}</link><pubDate>{date}</pubDate></item></channel></rss>'.encode()
    def test_only_today_timestamped_headlines_are_offered(self):
        self.assertEqual(len(parse_feed(self.raw(),self.feed,self.now)),1)
        self.assertEqual(parse_feed(self.raw(self.now-86400),self.feed,self.now),[])
        self.assertEqual(parse_feed(self.raw(self.now+3600),self.feed,self.now),[])
    def test_no_private_or_foreign_links_are_accepted(self):
        for url in ['http://www.chinanews.com.cn/a','https://127.0.0.1/a','https://evil.test/a','https://www.chinanews.com.cn.evil.test/a','https://u:p@www.chinanews.com.cn/a']:
            self.assertEqual(parse_feed(self.raw(url=url),self.feed,self.now),[])
    def test_xml_entity_and_oversized_payloads_are_rejected(self):
        self.assertEqual(parse_feed(b'<!DOCTYPE rss [<!ENTITY x "boom">]><rss/>',self.feed,self.now),[])
        self.assertEqual(parse_feed(b'x'*(MAX_BYTES+1),self.feed,self.now),[])
    def test_cache_avoids_repeated_fetch_and_is_invalid_next_day(self):
        calls=[]
        def fetch(feed):calls.append(feed['name']);return self.raw(url='https://'+feed['host']+'/a')
        provider=PublicTopics(self.root,lambda:self.now,fetch)
        self.assertEqual(len(provider.collect()['items']),2);provider.collect();self.assertEqual(len(calls),2)
        self.now+=86400;self.assertIsNone(provider.load());self.assertEqual(provider.snapshot()['cached_today_items'],0)
    def test_feed_failures_are_visible_without_blocking_conversation(self):
        def fail(feed):raise RuntimeError('private token must not be cached')
        value=PublicTopics(self.root,lambda:self.now,fail).collect()
        self.assertEqual(value['items'],[]);self.assertTrue(all(f['state']=='unavailable' for f in value['feeds']))
        self.assertNotIn('private token',json.dumps(value))
    def test_sources_expire_at_local_midnight(self):
        item=parse_feed(self.raw(),self.feed,self.now)
        self.assertEqual(len(current_sources(item,self.now)),1);self.assertEqual(current_sources(item,self.now+86400),[])
    def test_missing_dates_do_not_become_today(self):
        raw=self.raw().decode();start=raw.index('<pubDate>');end=raw.index('</pubDate>')+len('</pubDate>')
        self.assertEqual(parse_feed((raw[:start]+raw[end:]).encode(),self.feed,self.now),[])

if __name__=='__main__':unittest.main()
