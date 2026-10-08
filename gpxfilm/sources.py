"""Map backgrounds and official aerial imagery sources, and the source check (--check-maps)."""
from __future__ import annotations

import io
import math

import numpy as np
from PIL import Image

from .i18n import N_, _, credit, ui
from .net import http_get
from .tiles import tile_url

# Credits are English here and translated into the film language where they are drawn. Credits of several sources are joined
# with "; " and translated part by part, so a credit with "; " in it is made of marked parts.
SENTINEL = N_("Imagery: Sentinel-2 cloudless, s2maps.eu, EOX (Copernicus Sentinel data 2016–2017)")
TERRAIN = N_("Terrain: Mapterhorn")
MAPS = {   # ready map backgrounds from tiles: address, highest zoom, source credit
    "topo": ("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", 17, N_("Map: © OpenStreetMap, SRTM") + "; " + N_("style © OpenTopoMap (CC-BY-SA)")),
    "satellite": ("https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/g/{z}/{y}/{x}.jpg", 14,   # SENTINEL and TERRAIN, as one text
                 N_("Imagery: Sentinel-2 cloudless, s2maps.eu, EOX (Copernicus Sentinel data 2016–2017). Terrain: Mapterhorn. Trails: © OpenStreetMap")),
}
_WMS = "&STYLES=&CRS={proj}&BBOX={bbox}&FORMAT=image/jpeg&WIDTH={width}&HEIGHT={height}&VERSION=1.3.0&SERVICE=WMS&REQUEST=GetMap"
def _wms(base: str, layer: str, style: str = "") -> str:
    return base + ("&" if "?" in base else "?") + "LAYERS=" + layer + _WMS.replace("STYLES=", "STYLES=" + style)


# Official aerial photos published as open data: country code → list of sources (address, highest zoom, credit[, coverage]).
# Coverage is (west longitude, south latitude, east longitude, north latitude); sources without one cover the whole country.
# Order matters: the first source that covers the middle of the frame is the base, and the next ones fill its holes.
AERIAL = {
    "PL": [("https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMTS/StandardResolution?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=ORTOFOTOMAPA"
            "&STYLE=default&FORMAT=image/jpeg&tileMatrixSet=EPSG:3857&tileMatrix=EPSG:3857:{z}&tileRow={y}&tileCol={x}", 19,
            N_("Orthophotomap: Główny Urząd Geodezji i Kartografii, geoportal.gov.pl"))],
    "IT": [(_wms("https://geoservices.buergernetz.bz.it/mapproxy/p_bz-Orthoimagery/wms", "Aerial-2023-RGB"), 19,
            "Ortofoto 2023: © Provincia Autonoma di Bolzano – Alto Adige, CC BY 4.0", (10.38, 46.21, 12.48, 47.10)),
           (_wms("https://siat.provincia.tn.it/geoserver/stem/ecw-rgb-2015/wms", "ecw-rgb-2015"), 19,
            "Ortofoto 2015: © Provincia Autonoma di Trento", (10.44, 45.66, 11.97, 46.54)),
           ("http://wms.pcn.minambiente.it/ogc?MAP=/ms_ogc/WMS_v1.3/raster/ortofoto_colore_12.map&LAYERS=OI.ORTOIMMAGINI.2012.32,OI.ORTOIMMAGINI.2012.33"
            + _WMS.replace("STYLES=", "STYLES=,"), 18, "Ortofoto 2012: AGEA, Geoportale Nazionale (MASE)")],   # taken by AGEA, served by the ministry (MASE)
    "DE": [(_wms("https://geoservices.bayern.de/od/wms/dop/v1/dop20", "by_dop20c"), 19, "Luftbild: © Bayerische Vermessungsverwaltung", (8.97, 47.27, 13.84, 50.57)),
           (_wms("https://owsproxy.lgl-bw.de/owsproxy/ows/WMS_LGL-BW_ATKIS_DOP_20_C", "IMAGES_DOP_20_RGB"), 19, "Luftbild: © GeoBasis-DE / LGL-BW", (7.50, 47.53, 10.50, 49.80)),
           (_wms("https://www.wms.nrw.de/geobasis/wms_nw_dop", "nw_dop_rgb", "default"), 19, "Luftbild: © Geobasis NRW", (5.86, 50.32, 9.47, 52.54)),
           (_wms("https://www.gds-srv.hessen.de/cgi-bin/lika-services/ogc-free-images.ows", "he_dop20_rgb", "default"), 19, "Luftbild: © GeoBasis-DE / HVBG", (7.77, 49.39, 10.25, 51.66)),
           (_wms("https://geo4.service24.rlp.de/wms/rp_dop20.fcgi", "rp_dop20"), 19, "Luftbild: © GeoBasis-DE / LVermGeoRP", (6.11, 48.96, 8.51, 50.95)),
           (_wms("https://geoportal.saarland.de/freewms/truedop", "sl_dop20_rgb"), 19, "Luftbild: © GeoBasis-DE / LVGL-SL", (6.35, 49.10, 7.43, 49.65)),
           (_wms("https://geodienste.sachsen.de/wms_geosn_dop-rgb/guest", "sn_dop_020"), 19, "Luftbild: © GeoSN", (11.86, 50.16, 15.06, 51.70)),
           (_wms("https://www.geoproxy.geoportal-th.de/geoproxy/services/DOP", "th_dop"), 19, "Luftbild: © GDI-Th", (9.87, 50.20, 12.66, 51.65)),
           (_wms("https://www.geodatenportal.sachsen-anhalt.de/wss/service/ST_LVermGeo_DOP_WMS_OpenData/guest", "lsa_lvermgeo_dop20_2"), 19,
            "Luftbild: © GeoBasis-DE / LVermGeo LSA", (10.56, 50.93, 13.19, 53.05)),
           (_wms("https://isk.geobasis-bb.de/mapproxy/dop20c/service/wms", "bebb_dop20c"), 19, "Luftbild: © GeoBasis-DE / LGB", (11.26, 51.35, 14.80, 53.57)),
           (_wms("https://geodienste.hamburg.de/wms_dop_zeitreihe_belaubt", "dop_zeitreihe_belaubt"), 19, "Luftbild: © Freie und Hansestadt Hamburg, LGV", (8.40, 53.39, 10.34, 53.95)),
           (_wms("https://geodienste.bremen.de/wms_dop20_2023", "DOP20_2023_HB"), 19, "Luftbild: © GeoInformation Bremen", (8.45, 53.00, 9.00, 53.62)),
           (_wms("https://opendata.lgln.niedersachsen.de/doorman/noauth/dop_wms", "ni_dop20"), 19, "Luftbild: © LGLN", (6.30, 51.29, 11.61, 54.24)),
           (_wms("https://dienste.gdi-sh.de/WMS_SH_DOP20col_OpenGBD", "sh_dop20_rgb"), 19, "Luftbild: © GeoBasis-DE / LVermGeo SH", (7.86, 53.35, 11.33, 55.07)),
           (_wms("https://www.geodaten-mv.de/dienste/adv_dop", "mv_dop"), 19, "Luftbild: © GeoBasis-DE / M-V", (10.58, 53.10, 14.43, 54.70))],
    "AT": [("https://mapsneu.wien.gv.at/basemap/bmaporthofoto30cm/normal/google3857/{z}/{y}/{x}.jpeg", 19, "Orthofoto: basemap.at, CC BY 4.0")],
    "CH": [("https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.swissimage/default/current/3857/{z}/{x}/{y}.jpeg", 19, "Orthofoto: © swisstopo")],
    "FR": [("https://data.geopf.fr/tms/1.0.0/ORTHOIMAGERY.ORTHOPHOTOS/{z}/{x}/{y}.jpeg", 19, "Orthophoto: IGN, Licence Ouverte 2.0")],
    "ES": [("https://tms-pnoa-ma.idee.es/1.0.0/pnoa-ma/{z}/{x}/{-y}.jpeg", 19, "Ortofoto: PNOA, CC BY 4.0 scne.es")],
    "PT": [(_wms("https://cartografia.dgterritorio.gov.pt/wms/ortos2025", "Ortos2025-RGB"), 19, "Ortofotos 2025: Direção-Geral do Território")],
    "CZ": [("https://ags.cuzk.gov.cz/arcgis1/services/ORTOFOTO/MapServer/WMSServer?LAYERS=0" + _WMS, 19, "Ortofoto: © ČÚZK, CC BY 4.0")],
    "SK": [("https://ofmozaika.tiles.freemap.sk/{z}/{x}/{y}.jpg", 19, "Ortofotomozaika SR: © GKÚ Bratislava, NLC, CC BY 4.0")],
    "SI": [("https://gis.level2.si/geoserver/gwc/service/tms/1.0.0/level2%3ADOF025_latest@EPSG%3A3857@jpeg/{z}/{x}/{-y}.jpeg", 19,
            "Ortofoto DOF025: Geodetska uprava Republike Slovenije, CC BY 4.0")],
    "LU": [("https://wmts1.geoportail.lu/opendata/wmts/ortho_latest/GLOBAL_WEBMERCATOR_4_V3/{z}/{x}/{y}.jpeg", 19, "Orthophoto: Administration du cadastre et de la topographie, CC0")],
    "NL": [("https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=Actueel_orthoHR&STYLE=&FORMAT=image/jpeg"
            "&tileMatrixSet=OGC:1.0:GoogleMapsCompatible&tileMatrix={z}&tileRow={y}&tileCol={x}", 19, "Luchtfoto: Kadaster / Beeldmateriaal.nl, CC BY 4.0")],
    "BE": [("https://geo.api.vlaanderen.be/OMWRGBMRVL/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=omwrgbmrvl&STYLE=&FORMAT=image/png&tileMatrixSet=GoogleMapsVL"
            "&tileMatrix={z}&tileRow={y}&tileCol={x}", 19, "Luchtfoto: © Digitaal Vlaanderen", (2.50, 50.67, 5.93, 51.51)),
           (_wms("https://geoservices.wallonie.be/arcgis/services/IMAGERIE/ORTHO_LAST/MapServer/WmsServer", "0"), 19, "Orthophoto: © Service public de Wallonie", (2.80, 49.48, 6.42, 50.82))],
    "EE": [("https://tiles.maaamet.ee/tm/tms/1.0.0/foto@GMC/{z}/{x}/{-y}.jpg", 18, "Ortofoto: Maa- ja Ruumiamet")],
    "FI": [("https://tiles.kartat.kapsi.fi/ortokuva?LAYERS=ortokuva&STYLES=&SRS={proj}&BBOX={bbox}&FORMAT=image/jpeg&WIDTH={width}&HEIGHT={height}&VERSION=1.1.1&SERVICE=WMS&REQUEST=GetMap", 19,
            "Ortokuva: Maanmittauslaitos, CC BY 4.0")],
    # outside Europe
    "US": [("https://apps.geo.fpac.usda.gov/geo-imagery/rest/services/naip/conus_naip/ImageServer/exportImage?f=image&format=jpg&bbox={bbox}&bboxSR=3857&imageSR=3857&size={width},{height}", 18,
            "Imagery: USDA National Agriculture Imagery Program", (-125.0, 24.0, -66.5, 49.6)),
           ("https://imagery.geoplatform.gov/iipp/rest/services/NAIP/NAIP2021_Hawaii/ImageServer/exportImage?f=image&format=jpg&bbox={bbox}&bboxSR=3857&imageSR=3857&size={width},{height}", 18,
            "Imagery: USDA National Agriculture Imagery Program", (-160.6, 18.8, -154.7, 22.4)),
           ("https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}", 16, "Imagery: USGS The National Map")],
    "CA": [(_wms("https://ws.geoservices.lrc.gov.on.ca/arcgis5/services/AerialImagery/GEO_Imagery_Data_Service_2023to2027/ImageServer/WMSServer/", "GEO_Imagery_Data_Service_2023to2027:None"), 19,
            "Imagery: Open Government Licence – Ontario", (-83.5, 41.6, -74.3, 47.0)),
           (_wms("https://ws.geoservices.lrc.gov.on.ca/arcgis5/services/AerialImagery/GEO_Imagery_Data_Service_2018to2022/ImageServer/WMSServer/", "GEO_Imagery_Data_Service_2018to2022:None"), 19,
            "Imagery: Open Government Licence – Ontario", (-95.2, 41.6, -74.3, 51.5))],
    "JP": [("https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg", 18, "Imagery: Geospatial Information Authority of Japan (GSI)")],
    "AU": [("https://maps.six.nsw.gov.au/arcgis/rest/services/public/NSW_Imagery/MapServer/tile/{z}/{y}/{x}", 19,
            "Imagery: © State of New South Wales (Spatial Services), CC BY 4.0", (140.9, -37.6, 153.7, -28.1)),
           ("https://spatial-img.information.qld.gov.au/arcgis/rest/services/Basemaps/LatestStateProgram_AllUsers/ImageServer/WMTS/tile/1.0.0/Basemaps_LatestStateProgram_AllUsers/default/GoogleMapsCompatible/{z}/{y}/{x}", 19,
            "Imagery: © State of Queensland (Department of Resources), CC BY 4.0", (137.9, -29.2, 153.6, -9.0))],
    "AR": [("https://imagenes.ign.gob.ar/geoserver/gwc/service/tms/1.0.0/mosaicos_vuelos@EPSG%3A3857@png/{z}/{x}/{-y}.png", 19, "Ortofotos: Instituto Geográfico Nacional de la República Argentina")],
    "SG": [("https://www.onemap.gov.sg/maps/tiles/Satellite/{z}/{x}/{y}.png", 19, "Imagery: OneMap, © Singapore Land Authority")],
    "TW": [("https://wmts.nlsc.gov.tw/wmts/PHOTO2/default/GoogleMapsCompatible/{z}/{y}/{x}", 19, "Imagery: National Land Surveying and Mapping Center, Taiwan (NLSC)")],
    "HK": [("https://mapapi.geodata.gov.hk/gs/api/v1.0.0/xyz/imagery/WGS84/{z}/{x}/{y}.png", 19, "Imagery: Lands Department, Hong Kong SAR")],
}
AERIAL["LI"] = AERIAL["CH"]
# Probe points (latitude, longitude) for --check-maps, in the same order as the sources in AERIAL.
AERIAL_PROBES = {
    "PL": [(54.44, 18.56)], "IT": [(46.62, 12.30), (46.07, 11.12), (41.90, 12.48)],
    "DE": [(47.42, 10.98), (48.78, 9.18), (50.94, 6.96), (50.11, 8.68), (50.00, 8.27), (49.23, 7.00), (51.05, 13.74), (50.98, 11.03), (52.13, 11.63), (52.52, 13.40),
           (53.55, 9.99), (53.08, 8.80), (52.37, 9.73), (54.32, 10.13), (54.09, 12.13)],
    "AT": [(47.07, 12.69)], "CH": [(46.02, 7.75)], "FR": [(45.92, 6.87)], "ES": [(42.63, 0.66)], "PT": [(38.72, -9.14)], "CZ": [(50.74, 15.74)], "SK": [(49.16, 20.13)],
    "SI": [(46.37, 14.11)], "LU": [(49.61, 6.13)], "NL": [(52.09, 5.12)], "BE": [(51.05, 3.72), (50.46, 4.86)], "EE": [(59.44, 24.75)], "FI": [(60.17, 24.94)],
    "US": [(37.745, -119.59), (21.262, -157.806), (61.22, -149.89)], "CA": [(43.65, -79.38), (45.58, -78.36)], "JP": [(35.36, 138.73)], "AU": [(-33.72, 150.31), (-27.47, 153.02)],
    "AR": [(-34.60, -58.38)], "SG": [(1.29, 103.85)], "TW": [(25.03, 121.56)], "HK": [(22.28, 114.16)],
}


def check_maps() -> None:
    """Checks one tile from each aerial photo source and prints which sources answer."""
    from concurrent.futures import ThreadPoolExecutor

    def one(job):
        cc, (url, zmax, name, *rest), (lat, lon) = job
        z = min(15, zmax)
        n = 2 ** z
        x, y = int((lon + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
        try:
            a = np.asarray(Image.open(io.BytesIO(http_get(tile_url(url, z, x, y, 512 if "{bbox}" in url else 256), timeout=40, tries=1))).convert("RGB"))
            ok = float(a.std()) > 4                   # an even tile is missing data, not a photo
            return cc, ok, (_("working") if ok else _("answers with an empty image")), name
        except Exception as e:  # noqa: BLE001
            return cc, False, _("not working ({error})").format(error=f"{type(e).__name__}: {str(e)[:60]}"), name

    jobs = [(cc, src, pt) for cc in sorted(AERIAL_PROBES) for src, pt in zip(AERIAL[cc], AERIAL_PROBES[cc])]
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(one, jobs))
    for cc, ok, msg, name in res:
        status = _("OK") if ok else _("FAIL")
        print(f"{cc}  {status:4s}  {msg:44s}  {credit(name, ui())[:70]}")
    bad = sum(1 for r in res if not r[1])
    print()
    print(_("Sources: {total}, working: {working}, not working: {failing}. Where a source does not work, the program takes the next one "
            "or satellite imagery.").format(total=len(res), working=len(res) - bad, failing=bad))
