@echo off
setlocal EnableDelayedExpansion
title IP Address Monitor

:: ============================================================
:: IP Address Monitor
:: Shows local (LAN) IP, public IP, public IP's country, and
:: the country your DNS resolver appears to be querying from
:: (via Google's "o-o.myaddr.l.google.com" EDNS whoami trick),
:: then flags whether they match.
::
:: Requires: curl and nslookup (both built into Windows 10/11).
::
:: NOTE: ipinfo.io's free/anonymous tier is rate-limited
:: (roughly 1,000 requests/day). Refreshing every 1 second will
:: burn through that in under 5 minutes and you'll start seeing
:: "rate limit" errors instead of data. Bump REFRESH_SECONDS
:: below to something like 30-60 for sustained use.
:: ============================================================

set "REFRESH_SECONDS=1"
set Q=^"
set "TMPDIR=%TEMP%\ipmon"
if not exist "%TMPDIR%" mkdir "%TMPDIR%" >nul 2>&1

:loop
cls
echo ================================================================
echo   IP ADDRESS MONITOR  (refresh: %REFRESH_SECONDS%s)   %DATE% %TIME%
echo ================================================================
echo.

:: ---------------- Local IP(s) ----------------
echo [Local IP Address(es)]
set "FOUND_LOCAL=0"
for /f "tokens=2 delims=:" %%A in ('ipconfig ^| findstr /R /C:"IPv4 Address"') do (
    set "LIP=%%A"
    set "LIP=!LIP: =!"
    echo    !LIP!
    set "FOUND_LOCAL=1"
)
if "!FOUND_LOCAL!"=="0" echo    ^(none found^)
echo.

:: ---------------- Public IP + Country ----------------
set "PUB_IP="
set "PUB_COUNTRY="
curl -s -m 5 https://ipinfo.io/json > "%TMPDIR%\pub.json" 2>nul

for /f "usebackq tokens=2 delims=:," %%A in (`findstr /C:"\"ip\"" "%TMPDIR%\pub.json"`) do (
    set "PUB_IP=%%~A"
)
for /f "usebackq tokens=2 delims=:," %%A in (`findstr /C:"\"country\"" "%TMPDIR%\pub.json"`) do (
    set "PUB_COUNTRY=%%~A"
)
set "PUB_IP=!PUB_IP:"=!"
set "PUB_IP=!PUB_IP: =!"
set "PUB_COUNTRY=!PUB_COUNTRY:"=!"
set "PUB_COUNTRY=!PUB_COUNTRY: =!"

echo [Public (WAN) IP Address]
if defined PUB_IP (
    echo    !PUB_IP!  ^(country: !PUB_COUNTRY!^)
) else (
    echo    ^(lookup failed - no internet or ipinfo.io unreachable^)
)
echo.

:: ---------------- DNS resolver's apparent country ----------------
:: Asks Google's DNS servers directly "who is asking?" This shows
:: the public IP of the DNS resolver actually making queries on
:: your behalf, which can differ from your browsing IP if you use
:: a VPN, a third-party DNS provider (e.g. 8.8.8.8, 1.1.1.1), or
:: a proxy.
set "DNS_IP="
set "DNS_COUNTRY="
nslookup -type=TXT o-o.myaddr.l.google.com ns1.google.com > "%TMPDIR%\dns.txt" 2>nul

:: The IP comes back as a quoted string on its own line, e.g.:
::     o-o.myaddr.l.google.com     text =
::
::         "203.0.113.7"
:: so grab the first line whose trimmed content starts with a quote.
for /f "usebackq tokens=* delims=" %%L in ("%TMPDIR%\dns.txt") do (
    if not defined DNS_IP (
        set "DNS_LINE=%%L"
        for /f "tokens=* delims=	 " %%T in ("!DNS_LINE!") do set "DNS_TRIM=%%T"
        if "!DNS_TRIM:~0,1!"=="!Q!" set "DNS_IP=!DNS_TRIM!"
    )
)
if defined DNS_IP (
    set "DNS_IP=!DNS_IP:"=!"
    set "DNS_IP=!DNS_IP: =!"
    curl -s -m 5 https://ipinfo.io/!DNS_IP!/json > "%TMPDIR%\dnsgeo.json" 2>nul
    for /f "usebackq tokens=2 delims=:," %%A in (`findstr /C:"\"country\"" "%TMPDIR%\dnsgeo.json"`) do (
        set "DNS_COUNTRY=%%~A"
    )
    set "DNS_COUNTRY=!DNS_COUNTRY:"=!"
    set "DNS_COUNTRY=!DNS_COUNTRY: =!"
)

echo [DNS Resolver's Apparent Public IP ^& Country]
if defined DNS_IP (
    echo    !DNS_IP!  ^(country: !DNS_COUNTRY!^)
) else (
    echo    ^(lookup failed^)
)
echo.

:: ---------------- Comparison ----------------
echo [Comparison]
if defined PUB_COUNTRY if defined DNS_COUNTRY (
    if /I "!PUB_COUNTRY!"=="!DNS_COUNTRY!" (
        echo    MATCH - Your DNS queries appear to originate from the same
        echo    country ^(!PUB_COUNTRY!^) as your public browsing IP.
    ) else (
        echo    MISMATCH - Your public IP shows !PUB_COUNTRY! but your DNS
        echo    resolver appears to be querying from !DNS_COUNTRY!.
        echo    This can indicate a VPN, third-party DNS ^(e.g. 8.8.8.8,
        echo    1.1.1.1^), or a DNS leak.
    )
) else (
    echo    ^(unable to compare - one or both lookups failed^)
)

echo.
echo ================================================================
echo   Press Ctrl+C to stop.
echo ================================================================

"%SystemRoot%\System32\timeout.exe" /t %REFRESH_SECONDS% /nobreak >nul
goto loop
