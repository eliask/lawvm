"""Helpers for pinning corpus-dependent Finland tests to a fixed oracle version.

Every call to pinned_replay() locks the test to a specific Finlex consolidated
oracle artifact (e.g. finlex://sd-cons/1987/990/fin@20250740/main.xml). This
prevents silent drift when the corpus archive is updated with a newer
consolidation.

The oracle version pin usually also pins the amendment chain because
_resolve_applicable_amendment_records() filters amendments by
oracle_version_amendment_id (derived from the pinned oracle) in both
official_consolidation and legal_pit modes.

Exception: if the pinned oracle bytes themselves explicitly cite a later
cross-statute `source_vts_explicit` amendment, replay planning re-includes that
amendment so the replay stays commensurate with the oracle content rather than
with a stale embedded `fin@...` version pin.

To update a pin after a corpus refresh:
  uv run farchive locators data/finlex.farchive --pattern '%YEAR/NUM%' | grep 'sd-cons' | grep 'fin@'
Then update the ORACLE_VERSIONS entry and any changed assertions.
"""
from __future__ import annotations

from typing import Any

# Maps statute ID -> latest pinned oracle version tag.
# Updated by running: uv run farchive locators data/finlex.farchive --pattern '%YEAR/NUM%'
ORACLE_VERSIONS: dict[str, str] = {
    "1919/1-001": "19980567",
    "1947/625": "20191128",
    "1948/404": "20240118",
    "1956/463": "19950981",
    "1959/324": "20020863",
    "1969/327": "20050707",
    "1974/16":  "20250684",
    "1974/258": "20060711",
    "1981/555": "20250830",
    "1982/91": "19970465",
    "1982/1112": "20230014",
    "1982/710": "20221156",
    "1983/607": "19971413",
    "1984/603": "20260052",
    "1987/275": "20020074",
    "1987/1250":"20230604",
    "1987/990": "20250740",
    "1988/718": "19921015",
    "1989/819": "19991129",
    "1990/1039":"20081145",
    "1990/1247":"20221386",
    "1990/845": "20110427",
    "1991/1707":"20061322",
    "1991/827": "20241047",
    "1992/1282":"19990694",
    "1992/480": "20091516",
    "1993/1689":"19980626",
    "1993/1709":"20031231",
    "1993/58":  "20230630",
    "1993/615": "20251292",
    "1993/616": "20221323",
    "1994/1472":"20260185",
    "1995/1556":"20251350",
    "1995/370": "20241119",
    "1995/355": "20140077",
    "1995/386": "20130124",
    "1995/57": "20000235",
    "1997/108": "20030100",
    "1997/142": "19990786",
    # anaphoric pykälään+qualifier fix: §2 in chapter 5a now inserted by 2015/1752
    "1997/1339":"20151752",
    "1997/660": "19990022",
    "1997/746": "20001170",
    "1998/28":  "20250797",
    "2007/1321": "20091105",
    "1998/461": "20030750",
    "1999/488": "20251162",
    "2000/154": "20000154",
    "2000/252": "20200151",
    "2002/504": "20250723",
    "2002/1126": "20050886",
    "2002/1244":"20190140",
    "2002/885": "20251502",
    "2002/973": "20230781",
    "2003/343": "20161243",
    "2004/1287":"20221313",
    "2006/395": "20260059",
    "2006/766": "20240115",
    "2007/159": "20191197",
    "2007/508": "20111573",
    "2007/636": "20130392",
    "2008/550": "20251411",
    "2008/878": "20260355",
    "2009/1599":"20250537",
    "2009/1672":"20250706",
    "2009/953": "20190274",
    "2010/128": "20230947",  # recycle-and-rename regression
    "2010/1207":"20260024",
    "2010/76":  "20210674",  # whole-chapter replace drops omitted section
    "2010/182": "20251465",
    "2012/916": "20240443",
    "2013/393": "20250500",
    "2014/1429":"20251497",
    "2012/746": "20251423",
    "2014/346": "20150099",
    "2014/834": "20250041",
    "2015/1525":"20180802",
    "2015/242": "20251298",
    "2015/517": "20250636",
    "2016/1285":"20250621",
    "2016/258": "20211199",
    "2016/866": "20180261",
    "2017/277": "20251253",
    "2017/320": "20251001",
    "2017/445": "20210434",
    "2018/11":  "20260231",
    # §:GEN uusi N momentti insertion (Pattern B3) regression
    "2020/87":  "20200326",
    # INSERT chapter-remap guard: same-label section in different chapter must not block INSERT
    "2011/756": "20260073",
    # chapter-INSERT-duplicate regression statutes
    "1961/264": "20110274",
    "1974/412": "20101051",
    "1989/495": "20111492",
    "1997/689": "20260221",
    "2001/604": "20260009",
    "2009/1698":"20241072",
    # pseudo-chapter-marker restructuring: 1996/473 moves §55 from ch 7 → 7c
    "1988/161": "20251194",
    # top-level pseudo-chapter-marker (8 a luku) uncovered recovery: §72a/§72b/§72c
    "1977/603": "20250550",
    # orphaned UUSI body marker fix: 2022/958 multi-item lisätään with qualifier removal
    "1996/1260":"20260186",
    # Pins added to keep older Finland corpus regression tests off moving latest-oracle selection.
    "1868/31-000": "20030692",
    "1901/15-001": "19940390",
    "1920/26": "20230773",
    "1922/312": "19930869",
    "1929/234": "20250982",
    "1940/378": "20251349",
    "1947/328": "20150480",
    "1958/496": "20250543",
    "1953/317": "20050786",
    "1959/191": "20000671",
    "1962/184": "20041420",
    "1962/420": "20250307",
    "1965/40": "20250983",
    "1966/611": "20040817",
    "1966/612": "19840270",
    "1966/657": "20250661",
    "1967/543": "20251146",
    "1967/550": "20160717",
    "1967/551": "20130104",
    "1968/360": "20260023",
    "1973/36": "20161504",
    "1976/673": "20050756",
    "1978/38": "20260031",
    "1978/611": "20040568",
    "1979/1062": "20070870",
    "1982/716": "19990694",
    "1984/602": "20020137",
    "1984/719": "19961273",
    "1986/609": "20230101",
    "1987/1203": "20251258",
    "1987/322": "20250583",
    "1987/693": "20251439",
    "1988/1347": "20030252",
    "1990/1341": "20250378",
    "1991/1144": "20200911",
    "1991/1208": "20061451",
    "1992/110": "20221130",
    "1992/1702": "20020812",
    "1992/552": "20260014",
    "1992/772": "19970863",
    "1992/785": "20230785",
    "1993/1501": "20260025",
    "1994/201": "20231218",
    "1994/357": "19980600",
    "1994/674": "20250270",
    "1994/719": "20230043",
    "1995/509": "20030649",
    "1995/1760": "20041250",
    "1995/398": "20210973",
    "1996/1093": "20250685",
    "1996/1200": "20080377",
    "1996/931": "20260288",
    "1996/79": "19971227",
    "1996/1261": "19961261",
    "1996/1266": "20251359",
    "1996/627": "20230674",
    "1997/133": "20260130",
    "1998/986": "20250159",
    "1999/1352": "20250012",
    "1999/589": "19990589",
    "2000/609": "20061445",
    "2000/755": "20250085",
    "2000/812": "20230785",
    "2001/101": "20221363",
    "2001/1234": "20030811",
    "2002/1000": "20070180",
    "2002/64": "20060529",
    "2002/672": "20260018",
    "2002/1290": "20260049",
    "2002/1330": "20260174",
    "2002/197": "20150374",
    "2003/549": "20151159",
    "2004/1224": "20260055",
    "2004/137": "20230483",
    "2004/699": "20260183",
    "2005/452": "20120317",
    "2005/579": "20190379",
    "2006/1280": "20260061",
    "2006/624": "20250561",
    "2007/1024": "20251189",
    "2007/370": "20250099",
    "2009/617": "20210231",
    "2010/1048": "20250887",
    "2011/1546": "20161540",
    "2011/1552": "20250571",
    "2011/715": "20241039",
    "2012/980": "20240742",
    "2013/331": "20211030",
    "2013/588": "20251383",
    "2013/599": "20250981",
    "2014/1194": "20240984",
    "2014/255": "20220999",
    "2014/527": "20260189",
    "2014/938": "20260053",
    "2014/917": "20260021",
    "2015/1141": "20250441",
    "2015/351": "20200036",
    "2015/410": "20250586",
    "2016/1227": "20250788",
    "2016/1503": "20251050",
    "2016/549": "20250791",
    "2016/673": "20250835",
    "2016/768": "20260230",
    "2017/444": "20260184",
    "2017/519": "20251248",
    "2020/811": "20210407",
    "2021/1289": "20240420",
    "2021/616": "20251115",
    "2021/82": "20250458",
    "2022/1384": "20251260",
    "2022/213": "20260045",
    "2025/89": "29260224",
}


def pinned_replay(
    parent_id: str,
    oracle_version: str | None = None,
    **kwargs: Any,
) -> Any:
    """Replay with a pinned oracle version.

    If oracle_version is not given, looks up ORACLE_VERSIONS[parent_id].
    Raises ValueError if the statute is not in the map.

    The oracle pin normally also fixes the amendment chain, except for the
    explicit oracle-reflected `source_vts_explicit` override described above.
    All other replay_xml parameters (mode, stop_before, quiet, as_of, etc.)
    pass through unchanged.
    """
    from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector

    if oracle_version is None:
        oracle_version = ORACLE_VERSIONS.get(parent_id)
        if oracle_version is None:
            raise ValueError(
                f"pinned_replay: no oracle version pinned for {parent_id!r}. "
                f"Add it to ORACLE_VERSIONS in tests/corpus_pin_helpers.py."
            )

    selector = ConsolidatedArtifactSelector.exact_embedded_version(oracle_version)
    return replay_xml_for_test(parent_id, oracle_selector=selector, **kwargs)


def replay_xml_for_test(parent_id: str, **kwargs):
    """Typed replay_xml bridge for tests that still use legacy-shaped inputs."""
    from lawvm.finland.replay_entrypoint import replay_xml
    from lawvm.finland.replay_request import ReplayXmlRequest, ReplayXmlSinks

    request = ReplayXmlRequest(
        parent_id=parent_id,
        mode=kwargs.pop("mode", "official_consolidation"),
        stop_before=kwargs.pop("stop_before", ""),
        strict_profile=kwargs.pop("strict_profile", None),
        corpus=kwargs.pop("corpus", None),
        quiet=kwargs.pop("quiet", False),
        build_full_products=kwargs.pop("build_full_products", True),
        checkpoint_callback=kwargs.pop("checkpoint_callback", None),
        as_of=kwargs.pop("as_of", ""),
        strict_johto_temporal=kwargs.pop("strict_johto_temporal", False),
        oracle_selector=kwargs.pop("oracle_selector", None),
    )
    sinks = ReplayXmlSinks(
        compiled_ops_out=kwargs.pop("compiled_ops_out", None),
        replay_meta_out=kwargs.pop("replay_meta_out", None),
        lo_ops_out=kwargs.pop("lo_ops_out", None),
        failed_ops_out=kwargs.pop("failed_ops_out", None),
        temporal_events_out=kwargs.pop("temporal_events_out", None),
        source_pathologies_out=kwargs.pop("source_pathologies_out", None),
    )
    if kwargs:
        raise TypeError(f"unexpected replay_xml_for_test kwargs: {sorted(kwargs)}")
    return replay_xml(request=request, sinks=sinks)
