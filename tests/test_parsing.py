import unittest

from src.collectors import parsing as P

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
 <entry>
  <id>http://arxiv.org/abs/1601.02063v2</id>
  <published>2016-01-08T19:04:34Z</published>
  <title>Odyssey:  A Public
   GPU-Based Code</title>
  <author><name>Ann Lee</name></author><author><name> Bo  Chen </name></author>
  <arxiv:primary_category term="astro-ph.HE"/>
  <category term="astro-ph.HE"/><category term="cs.LG"/>
 </entry>
 <entry>
  <id>http://arxiv.org/abs/hep-th/9901001v1</id>
  <published>1999-01-01T00:00:00Z</published>
  <title>Old style id</title>
  <author><name>Z Z</name></author>
  <arxiv:primary_category term="cs.AI"/>
 </entry>
 <entry>
  <id>http://arxiv.org/api/errors#incorrect_id_format</id>
  <title>Error</title>
 </entry>
</feed>"""


class ParsingTests(unittest.TestCase):
    def test_github_repo(self):
        f = P.parse_github_repo
        self.assertEqual(f("https://github.com/jmaasch/itpd"), "jmaasch/itpd")
        self.assertEqual(f("https://github.com/a/b.git"), "a/b")
        self.assertEqual(f("http://www.github.com/a/b/tree/main/x"), "a/b")
        self.assertEqual(f("https://github.com/a/foo.js"), "a/foo.js")
        self.assertIsNone(f("https://github.com/a"))
        self.assertIsNone(f("https://github.com/orgs/x"))
        self.assertIsNone(f("https://gitlab.com/a/b"))
        self.assertIsNone(f(None))

    def test_pwc_rows(self):
        data = {"rows": [
            {"row": {"paper_arxiv_id": "1810.10559", "repo_url": "https://github.com/p/q", "is_official": "true"}},
            {"row": {"paper_arxiv_id": "null", "repo_url": "https://github.com/p/q", "is_official": "true"}},
            {"row": {"paper_arxiv_id": "1601.02063", "repo_url": "https://example.com/x", "is_official": "false"}},
            {"row": {"paper_arxiv_id": "1601.02064", "repo_url": "https://github.com/r/s", "is_official": "false"}},
        ]}
        rows = P.parse_pwc_rows(data)
        self.assertEqual([r["arxiv_id"] for r in rows], ["1810.10559", "1601.02064"])
        self.assertTrue(rows[0]["is_official"])
        self.assertFalse(rows[1]["is_official"])
        self.assertEqual(P.parse_pwc_rows(None), [])

    def test_arxiv_atom(self):
        items = P.parse_arxiv_atom(ATOM)
        self.assertEqual(len(items), 2)  # error entry skipped
        a = items[0]
        self.assertEqual(a["arxiv_id"], "1601.02063")
        self.assertEqual(a["title"], "Odyssey: A Public GPU-Based Code")
        self.assertEqual(a["authors"], ["Ann Lee", "Bo Chen"])
        self.assertEqual(a["primary_category"], "astro-ph.HE")
        self.assertEqual(items[1]["arxiv_id"], "hep-th/9901001")

    def test_ai_category(self):
        self.assertTrue(P.is_ai_category("cs.LG"))
        self.assertFalse(P.is_ai_category("astro-ph.HE"))
        self.assertFalse(P.is_ai_category(None))

    def test_ai_startup_and_employee_count(self):
        yes = {"one_liner": "AI copilots for lawyers", "tags": [], "industries": []}
        yes2 = {"one_liner": "x", "tags": ["Generative AI"], "industries": []}
        yes3 = {"one_liner": "Machine learning for farms"}
        no = {"one_liner": "Email for dentists", "tags": ["Health"], "industries": ["Healthcare"]}
        tricky = {"one_liner": "Paid ads", "tags": ["Marketing"]}  # 'ai' inside words must not match
        self.assertTrue(P.is_ai_startup(yes) and P.is_ai_startup(yes2) and P.is_ai_startup(yes3))
        self.assertFalse(P.is_ai_startup(no) or P.is_ai_startup(tricky))
        self.assertEqual(P.employee_count({"team_size": 58}), 58)
        self.assertIsNone(P.employee_count({"team_size": 0}))
        self.assertIsNone(P.employee_count({}))


if __name__ == "__main__":
    unittest.main()


class PricingTests(unittest.TestCase):
    PAD = " Acme builds tools for teams. " * 12  # pages under 200 chars are treated as JS shells

    def test_html_text_and_links(self):
        html = ("<html><head><style>.a{}</style><script>var x=1</script></head><body>"
                "<a href='/pricing'>Pricing</a><a href='https://other.com/pricing'>Pricing</a>"
                "<a href='/blog'>Blog</a><a href='/plans'>Our plans</a><p>Hello <b>world</b></p></body></html>")
        self.assertEqual(P.html_to_text(html), "Pricing Pricing Blog Our plans Hello world")
        links = P.find_pricing_links(html, "https://www.acme.com/")
        self.assertEqual(links[0], "https://www.acme.com/pricing")
        self.assertIn("https://www.acme.com/plans", links)
        self.assertNotIn("https://other.com/pricing", links)

    def test_classify(self):
        c = P.classify_pricing
        self.assertEqual(c(self.PAD + "Free plan: $0. Pro: $20/month per user.")[0], "FREEMIUM")
        self.assertEqual(c(self.PAD + "Pro $49 per month. Team $99 / user.")[0], "PAID")
        self.assertEqual(c(self.PAD + "Free forever. No credit card. Open to everyone.")[0], "FREE")
        self.assertEqual(c(self.PAD + "Enterprise plans. Contact sales for custom pricing.")[0], "ENTERPRISE")
        # "Start for free" is a weak signal (often a trial): with prices -> PAID, alone -> ambiguous
        self.assertEqual(c(self.PAD + "Start for free. Then $12/mo.")[0], "PAID")
        self.assertEqual(c(self.PAD + "Start for free today.")[0], None)
        self.assertEqual(c(self.PAD + "Start free trial. Pro $30/mo.")[0], "PAID")
        # "enterprise-grade" marketing copy alone is not evidence of enterprise pricing
        self.assertEqual(c(self.PAD + "Enterprise-grade security for everyone.")[0], None)
        label, ev = c(self.PAD + "Pro $49 per month.")
        self.assertTrue(any("$49 per month" in e for e in ev))  # evidence is verbatim page text
        self.assertEqual(c(self.PAD + "We build great things.")[0], None)
        self.assertEqual(c("short page")[0], None)
        # prices present in a format the strict regex misses must not become ENTERPRISE
        self.assertEqual(c(self.PAD + "Plans from $29 billed annually. Contact sales for Scale.")[0], None)
        self.assertEqual(c(self.PAD + "Free plan. Pro $29 billed annually.")[0], "FREEMIUM")

    def test_pricing_link_not_demo_page(self):
        html = "<a href='/book-a-demo'>Pricing</a><a href='/pricing'>Plans</a>"
        self.assertEqual(P.find_pricing_links(html, "https://acme.com/"), ["https://acme.com/pricing"])
        self.assertEqual(P.find_pricing_links("<a href='/book-a-demo'>Pricing</a>", "https://acme.com/"), [])


class PricingRegressionTests(unittest.TestCase):
    """Cases found by auditing real pages."""
    PAD = " Acme builds tools for teams. " * 12

    def test_decimal_prices_are_not_free(self):
        c = P.classify_pricing
        self.assertEqual(c(self.PAD + "Pay per match $ 0.065 / email. Verified mobile $0.0001 / transaction.")[0], "PAID")
        self.assertEqual(c(self.PAD + "Free $0.00 / month. Pro $25 / month.")[0], "FREEMIUM")

    def test_nav_links_are_not_enterprise_evidence(self):
        c = P.classify_pricing
        self.assertEqual(c(self.PAD + "Contact Us Book a demo Schedule a demo Log in")[0], None)
        self.assertEqual(c(self.PAD + "Custom plans. Contact sales for a quote.")[0], "ENTERPRISE")

    def test_free_then_upgrade_is_freemium(self):
        c = P.classify_pricing
        self.assertEqual(c(self.PAD + "Start for free, then upgrade to Pro. Individual $20 /mo")[0], "FREEMIUM")


class PricingRegressionTests2(unittest.TestCase):
    PAD = " Acme builds tools for teams. " * 12

    def test_audit_round_two(self):
        c = P.classify_pricing
        # pay-as-you-go base fee is not a free plan
        self.assertEqual(c(self.PAD + "Start 14-day trial Pay as you go $0 /month + usage. Flat $500/mo.")[0], "PAID")
        # a $0 24-hour trial is not a free plan
        self.assertEqual(c(self.PAD + "Free $0 /24-hour trial Try it on a shared line. Shared $25 /month")[0], "PAID")
        # "$0k" slider labels are not a free plan
        self.assertEqual(c(self.PAD + "$0k $10k $25k $50k Select expected spend $ 599 monthly or $ 5990 annually")[0], "PAID")
        # "Start for free" followed by an upgrade path = free plan
        self.assertEqual(c(self.PAD + "Point of Sale Start for free Upgrade to Online Store Pro ($59/mo)")[0], "FREEMIUM")
        # real free plans still work
        self.assertEqual(c(self.PAD + "Free $0/mo. plus usage costs. Pro $25 / month")[0], "FREEMIUM")
        self.assertEqual(c(self.PAD + "Starter $ 0 /month Free for everyone. Pro $ 250 /month")[0], "FREEMIUM")


class PricingRegressionTests3(unittest.TestCase):
    PAD = " Acme builds tools for teams. " * 12

    def test_dollar_zero_today_is_a_trial(self):
        c = P.classify_pricing
        self.assertEqual(c(self.PAD + "Start 7-day free trial $0 today with a card. $75 after your 7-day trial. Starter $ 75 /month")[0], "PAID")
        self.assertEqual(c(self.PAD + "Free $0 /month. Pro $20 /month")[0], "FREEMIUM")

    def test_pricing_url_check(self):
        self.assertTrue(P.looks_like_pricing_url("https://a.com/en/pricing"))
        self.assertTrue(P.looks_like_pricing_url("https://a.com/plans"))
        self.assertFalse(P.looks_like_pricing_url("https://a.com/"))
        self.assertFalse(P.looks_like_pricing_url("https://a.com/demo"))


class PricingRegressionTests4(unittest.TestCase):
    PAD = " Acme builds tools for teams. " * 12

    def test_audit_round_three(self):
        c = P.classify_pricing
        self.assertEqual(c(self.PAD + "Benefits Health, Dental, Vision $0 + carrier premiums. Starting at $ 25 /month (per employee)")[0], "PAID")
        self.assertEqual(c(self.PAD + "Pay as you go $0 then $0.05 / credit. About $0.50 per GB.")[0], "PAID")
        self.assertEqual(c(self.PAD + "Free Full platform preview $0 / 3 school days. School license $ 695 /school/year")[0], "PAID")
        self.assertEqual(c(self.PAD + "$0 platform fee. Machine runtime $0.0414 per GiB per hour.")[0], "PAID")
        # real free tiers still classified as free
        self.assertEqual(c(self.PAD + "No credit card required. $0 Shared phone number. $100 /month Per dedicated line. $0 Per-message fees")[0], "FREEMIUM")
        self.assertEqual(c(self.PAD + "Open Source $0 Free forever self-hosted. Enterprise Talk to sales")[0], "FREEMIUM")

    def test_llm_candidate_filter(self):
        ok = ["https://a.com/pricing", "https://a.com/en/pricing/", "https://a.com/plans"]
        bad = ["https://a.com/", "https://a.com/demo", "https://a.com/authentication?next=/pricing",
               "https://a.com/blog/the-truth-about-surge-pricing", "https://docs.a.com/pricing/pricing-optimization",
               "https://a.com/contact", "https://a.com/solutions/startups/pricing"]
        for u in ok:
            self.assertTrue(P.is_llm_pricing_candidate(u), u)
        for u in bad:
            self.assertFalse(P.is_llm_pricing_candidate(u), u)


class PricingRegressionTests5(unittest.TestCase):
    PAD = " Acme builds tools for teams. " * 12

    def test_audit_round_four(self):
        c = P.classify_pricing
        # "$0 volume" is a pricing-formula term, not a free plan
        self.assertEqual(c(self.PAD + "Team pricing scales from $5,000/year at $0 volume up to the plan price. Plan $850/mo.")[0], "PAID")
        # slider labels "$0 $15M $30M" are not a free plan
        self.assertEqual(c(self.PAD + "Clients combined $43,500,000 $0 $15M $30M $45M. Everything you get for $50 a month.")[0], "PAID")
        # a free plan listed next to other $0 prices is still free
        self.assertEqual(c(self.PAD + "Team Free $0 $0 $15 billed monthly. Pro $25 /month")[0], "FREEMIUM")


class LLMEvidenceTests(unittest.TestCase):
    def test_free_labels_need_real_free_evidence(self):
        f = P.llm_label_supported
        self.assertFalse(f("FREEMIUM", "Start for Free"))                       # bare CTA (often a trial)
        self.assertTrue(f("FREEMIUM", "SELF-SERVE Free For a Shopify brand that wants to build its own app"))
        self.assertTrue(f("FREEMIUM", "Free plan $0 /month"))
        self.assertTrue(f("FREEMIUM", "Free forever, within the caps."))
        self.assertTrue(f("FREEMIUM", "Empieza gratis con créditos, o suscríbete desde $1,999 MXN/mes"))
        self.assertFalse(f("FREE", "Pro $20 per user"))

    def test_paid_needs_a_price(self):
        f = P.llm_label_supported
        self.assertTrue(f("PAID", "Starting from $50K /year"))
        self.assertTrue(f("PAID", "Pro $20 per user"))
        self.assertFalse(f("PAID", "Welcome to our product"))

    def test_enterprise_needs_contact_evidence_and_no_prices(self):
        f = P.llm_label_supported
        self.assertTrue(f("ENTERPRISE", "Request Pricing", "Welcome. Request Pricing to get started."))
        self.assertTrue(f("ENTERPRISE", "Get in touch via email , or book a call .", "no prices here"))
        self.assertFalse(f("ENTERPRISE", "Request Pricing", "Starter $20 /month. Request Pricing for Enterprise"))
        self.assertFalse(f("ENTERPRISE", "Welcome to our product", "nothing"))
        self.assertFalse(f("UNKNOWN", "anything"))


class LLMEvidenceTests2(unittest.TestCase):
    """Valid quotes that the first version of the check wrongly rejected (from a real run)."""

    def test_enterprise_no_public_price_phrasings(self):
        f = P.llm_label_supported
        self.assertTrue(f("ENTERPRISE", "Durate does not publish a price list; no one in physician scheduling does.", "no prices"))
        self.assertTrue(f("ENTERPRISE", "Answer six questions, and we'll price a proposal based on your headcount.", "no prices"))
        self.assertTrue(f("ENTERPRISE", "Request a Quote", "We raised $2.5M. Request a Quote"))   # a funding figure is not a price
        self.assertFalse(f("ENTERPRISE", "Request a Quote", "Pro $25 /month. Request a Quote"))   # real prices present

    def test_paid_other_currencies(self):
        f = P.llm_label_supported
        self.assertTrue(f("PAID", "Standard Rs. 1999 Rs.999 30 Days Free Trial"))
        self.assertTrue(f("PAID", "FlatX Pro Pricing - FlatX Full Access Plans (₹1499)"))
        self.assertFalse(f("PAID", "We will charge you only for the revenue we recover above your base line."))
        self.assertFalse(f("PAID", "Our users 5 stars"))      # "rs 5" inside "users 5" is not a currency

    def test_free_tier_phrasings(self):
        f = P.llm_label_supported
        self.assertTrue(f("FREEMIUM", "Start for free Pay nothing for up to $2,500 in monthly tracked revenue."))
        self.assertTrue(f("FREEMIUM", "Open Source MIT License A production-grade, double-entry core ledger."))
        self.assertTrue(f("FREEMIUM", "Free For developers building and testing with Mem0."))
        self.assertFalse(f("FREEMIUM", "Start for Free"))
