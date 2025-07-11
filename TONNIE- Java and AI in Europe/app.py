import streamlit as st
import math
import pandas as pd
from datetime import datetime
import yfinance as yf
import os
import smtplib
from email.mime.text import MIMEText
import feedparser
from tradingview_ta import TA_Handler, Interval, get_multiple_analysis
import time
import requests
import plotly.express as px
import numpy as np # Added for numerical operations in backtesting
import bcrypt # Importa bcrypt para hash de senhas

# --- Funções de Análise ---

def calcular_preco_justo_graham(info: dict, taxa_crescimento_anual: float, y_bond_yield: float):
    """[MODELO 1] Calcula o preço justo de uma ação usando a fórmula de Benjamin Graham."""
    try:
        ticker = info.get('symbol')

        lpa = info.get('trailingEps')
        preco_atual = info.get('currentPrice') or info.get('regularMarketPrice')

        if lpa is None:
            return {"erro": f"Não foi possível encontrar o Lucro Por Ação (LPA) para o ticker {ticker}. "
                            f"Isso pode ocorrer se o ativo for um ETF, FII, BDR ou empresa sem lucro reportado nos últimos 12 meses."}

        if lpa <= 0:
            return {"erro": f"O modelo de Graham não se aplica a empresas com lucro negativo. LPA atual: {lpa}"}

        if preco_atual is None:
             return {"erro": f"Não foi possível encontrar o preço atual para o ticker {ticker}."}

        # Fórmula de Graham Revisada: Preço Justo = (LPA * (8.5 + 2g) * 4.4) / Y
        preco_justo = (lpa * (8.5 + 2 * taxa_crescimento_anual) * 4.4) / y_bond_yield

        resultado = {
            "ticker": ticker,
            "modelo": "Preço Justo de Graham",
            "valor_calculado": preco_justo,
            "preco_atual": preco_atual,
            "lpa_ultimos_12m": lpa,
            "bond_yield_usado_%": y_bond_yield,
            "taxa_crescimento_usada_%": taxa_crescimento_anual
        }

        margem_seguranca = ((preco_justo - preco_atual) / preco_atual) * 100
        resultado["margem_seguranca_%"] = margem_seguranca
        
        return resultado

    except Exception as e:
        return {"erro": "Dados indisponíveis no momento, tente novamente mais tarde."}

def calcular_numero_graham(info: dict):
    """[MODELO 2] Calcula o Número de Graham para uma ação."""
    try:
        ticker = info.get('symbol')
        lpa = info.get('trailingEps')
        vpa = info.get('bookValue')
        preco_atual = info.get('currentPrice') or info.get('regularMarketPrice')

        if lpa is None or vpa is None:
            return {"erro": f"Não foi possível obter LPA ou VPA para '{ticker}'. O dado pode não estar disponível."}

        if lpa <= 0 or vpa <= 0:
            return {"erro": f"O Número de Graham não se aplica a empresas com LPA ou VPA negativos. LPA: {lpa:.2f}, VPA: {vpa:.2f}"}

        numero_graham = math.sqrt(22.5 * lpa * vpa)

        resultado = {
            "ticker": ticker,
            "modelo": "Número de Graham (Valor)",
            "valor_calculado": numero_graham,
            "preco_atual": preco_atual,
            "lpa_ultimos_12m": lpa,
            "vpa": vpa
        }

        margem_seguranca = ((numero_graham - preco_atual) / preco_atual) * 100
        resultado["margem_seguranca_%"] = margem_seguranca

        return resultado
    except Exception as e:
        return {"erro": "Dados indisponíveis no momento, tente novamente mais tarde."}

def calcular_preco_teto_bazin(info: dict):
    """[MODELO 3] Calcula o Preço-Teto de Bazin, usando a média dos dividendos dos últimos 5 anos."""
    try:
        ticker = info.get('symbol')
        dividendos_hist = info.get('dividendos')
        preco_atual = info.get('currentPrice') or info.get('regularMarketPrice')

        if dividendos_hist is None or dividendos_hist.empty:
            return {"erro": f"O modelo de Bazin não se aplica. A empresa '{ticker}' não tem histórico de dividendos."}

        # Garante que os datetimes sejam timezone-naive para evitar erros de comparação.
        # Faz uma cópia para não modificar o objeto original em cache.
        dividendos_hist_naive = dividendos_hist.copy()
        if dividendos_hist_naive.index.tz is not None:
            dividendos_hist_naive.index = dividendos_hist_naive.index.tz_localize(None)

        # Calcula a média dos dividendos dos últimos 5 anos
        hoje = pd.to_datetime('today').normalize() # .normalize() zera a hora e mantém naive
        cinco_anos_atras = hoje - pd.DateOffset(years=5)
        
        dividendos_5a = dividendos_hist_naive[dividendos_hist_naive.index > cinco_anos_atras]
        
        if dividendos_5a.empty:
            return {"erro": f"O modelo de Bazin não se aplica. A empresa '{ticker}' não pagou dividendos nos últimos 5 anos."}

        soma_dividendos_5a = dividendos_5a.sum()
        media_anual_dividendos = soma_dividendos_5a / 5

        preco_teto = media_anual_dividendos / 0.06

        resultado = {
            "ticker": ticker,
            "modelo": "Preço-Teto Bazin (Média 5A)",
            "valor_calculado": preco_teto,
            "preco_atual": preco_atual,
            "media_dividendo_anual_5a": media_anual_dividendos
        }

        margem_seguranca = ((preco_teto - preco_atual) / preco_atual) * 100
        resultado["margem_seguranca_%"] = margem_seguranca
        return resultado
    except Exception:
        return {"erro": "Dados indisponíveis no momento, tente novamente mais tarde."}

def calcular_preco_teto_fii(info: dict, dy_desejado: float):
    """[MODELO 4] Calcula o Preço-Teto para FIIs com base no DY desejado."""
    try:
        ticker = info.get('symbol')
        preco_atual = info.get('currentPrice') or info.get('regularMarketPrice')
        
        # yfinance's trailingAnnualDividendRate is the sum of dividends over the past 12 months.
        dividendo_anual = info.get('trailingAnnualDividendRate')

        if dividendo_anual is None or dividendo_anual <= 0:
            return {"erro": f"Não foi possível encontrar o dividendo anualizado para '{ticker}'. O modelo não pode ser aplicado."}
        
        if dy_desejado <= 0:
            return {"erro": "O Dividend Yield desejado deve ser maior que zero."}

        preco_teto = dividendo_anual / (dy_desejado / 100.0)

        resultado = {
            "ticker": ticker, "modelo": "Preço-Teto por DY", "valor_calculado": preco_teto,
            "preco_atual": preco_atual, "dividendo_anual_pago": dividendo_anual, "dy_desejado_%": dy_desejado
        }

        margem_seguranca = ((preco_teto - preco_atual) / preco_atual) * 100
        resultado["margem_seguranca_%"] = margem_seguranca
        return resultado
    except Exception:
        return {"erro": "Dados indisponíveis no momento, tente novamente mais tarde."}

@st.cache_resource(ttl=3600) # Cache o objeto Ticker por 1 hora
def _get_yf_ticker_resource(ticker: str):
    """
    Cria e armazena em cache um objeto yfinance Ticker.
    Este objeto é um recurso e será reutilizado.
    """
    return yf.Ticker(ticker)

@st.cache_data(ttl=3600) # Cache de 1 hora
def obter_dados_acao(ticker: str):
    """
    Busca e valida os dados de uma ação usando a biblioteca yfinance.
    """
    try:
        ticker_obj = yf.Ticker(ticker)
        ticker_obj = _get_yf_ticker_resource(ticker) # Obtenha o recurso Ticker cacheado
        # A chamada .info é a principal. Se falhar, o ticker provavelmente não existe.
        info = ticker_obj.info
        # Validação: um ticker válido deve retornar um dicionário de informações com um preço.
        if not info or (info.get('currentPrice') is None and info.get('regularMarketPrice') is None):
             return {"erro": f"Não foi possível encontrar dados para o ticker '{ticker}'. Verifique se o ticker está correto ou se há dados de preço disponíveis."}

        # Busca os outros dados necessários
        historico_precos = ticker_obj.history(period="max")
        dividendos = ticker_obj.dividends
        balance_sheet = ticker_obj.balance_sheet
        financials = ticker_obj.financials

        # Ensure these are DataFrames and not empty before storing
        if not isinstance(balance_sheet, pd.DataFrame) or balance_sheet.empty:
            balance_sheet = pd.DataFrame()
        if not isinstance(financials, pd.DataFrame) or financials.empty:
            financials = pd.DataFrame()


        # Estrutura os dados no formato esperado pelo resto da aplicação
        dados = {
            'symbol': info.get('symbol'),
            'longName': info.get('longName') or info.get('shortName'),
            'quoteType': info.get('quoteType'),
            'currentPrice': info.get('currentPrice') or info.get('regularMarketPrice'),
            'trailingEps': info.get('trailingEps'),
            'bookValue': info.get('bookValue'),
            'dividendYield': info.get('dividendYield'),
            'trailingAnnualDividendRate': info.get('trailingAnnualDividendRate'),
            'trailingPE': info.get('trailingPE'),
            'priceToBook': info.get('priceToBook'),
            'returnOnEquity': info.get('returnOnEquity'),
            'enterpriseValue': info.get('enterpriseValue'),
            'ebitda': info.get('ebitda'),
            'historico_precos': historico_precos.rename(columns={'Close': 'Close'}), # Garante nome da coluna
            'dividendos': dividendos.rename("Dividends"), # Garante nome da série
            'balance_sheet': balance_sheet,
            'quarterly_balance_sheet': ticker_obj.quarterly_balance_sheet, # Adicionado para dados trimestrais
            'financials': financials,
            'quarterly_financials': ticker_obj.quarterly_financials, # Adicionado para dados trimestrais
        }
        return dados

    except Exception as e:
        return {"erro": "Dados indisponíveis no momento, tente novamente mais tarde."}

def exibir_resultados_comparativos(resultados: list):
    """Exibe os resultados dos diferentes modelos em colunas para comparação."""
    st.subheader("Painel de Análise Comparativa")
    
    sucessos = [r for r in resultados if "erro" not in r]
    erros = [r for r in resultados if "erro" in r]

    if not sucessos:
        st.error("Nenhum modelo pôde ser aplicado com sucesso para este ticker.")
    else:
        cols = st.columns(len(sucessos))
        for i, res in enumerate(sucessos):
            with cols[i]:
                st.subheader(res['modelo'])
                st.metric(
                    label="Valor Calculado",
                    value=f"R$ {res['valor_calculado']:.2f}",
                    delta=f"{res['margem_seguranca_%']:.2f}% vs Preço Atual",
                    delta_color="normal"
                )
                st.metric(label="Preço Atual", value=f"R$ {res['preco_atual']:.2f}")
                
                with st.expander("Detalhes"):
                    details_to_show = res.copy()
                    for key in ['ticker', 'modelo', 'valor_calculado', 'preco_atual', 'margem_seguranca_%', 'erro']:
                        details_to_show.pop(key, None)

                    if not details_to_show:
                        st.text("Nenhum detalhe adicional para este modelo.")
                    
                    for chave, valor in details_to_show.items():
                        label = chave.replace('_', ' ').replace('%', '').title()
                        if chave.endswith("_%"):
                            st.markdown(f"**{label}:** `{valor:.2f}%`")
                        elif isinstance(valor, float):
                            st.markdown(f"**{label}:** `R$ {valor:.2f}`")
                        else:
                            st.markdown(f"**{label}:** `{valor}`")

    for erro in erros:
        st.warning(f"**Modelo {erro.get('modelo', '')} não aplicável:** {erro['erro']}")

def exibir_indicadores_chave(dados_acao: dict):
    """Exibe um painel com os principais indicadores fundamentalistas."""
    st.subheader("Indicadores Fundamentais")

    def get_financial_metric(dataframe, key):
        if dataframe is None or dataframe.empty or key not in dataframe.index:
            return None
        value = dataframe.loc[key, dataframe.columns[0]]
        return value if pd.notna(value) else None

    balance_sheet = dados_acao.get('balance_sheet')
    financials = dados_acao.get('financials')
    dy = dados_acao.get('dividendYield')
    total_debt = get_financial_metric(balance_sheet, 'Total Liab')
    total_equity = get_financial_metric(balance_sheet, 'Total Stockholder Equity')
    debt_to_equity_calculado = None
    if total_debt is not None and total_equity is not None and total_equity > 0:
        debt_to_equity_calculado = total_debt / total_equity

    net_income = get_financial_metric(financials, 'Net Income')
    roe_calculado = None
    if net_income is not None and total_equity is not None and total_equity > 0:
        roe_calculado = net_income / total_equity

    enterprise_value = dados_acao.get('enterpriseValue')
    ebitda = dados_acao.get('ebitda')
    ev_ebitda_calculado = None
    if enterprise_value is not None and ebitda is not None and ebitda > 0:
        ev_ebitda_calculado = enterprise_value / ebitda

    indicadores = {
        "P/L (Preço/Lucro)": dados_acao.get('trailingPE'),
        "P/VP (Preço/Valor Pat.)": dados_acao.get('priceToBook'),
        "Dividend Yield": dy,
        "ROE (Retorno/Pat. Líq.)": roe_calculado,
        "Dív. Bruta/Patrimônio": debt_to_equity_calculado,
        "EV/EBITDA": ev_ebitda_calculado
    }

    indicadores_validos = {k: v for k, v in indicadores.items() if pd.notna(v)}

    if not indicadores_validos:
        st.info("Não há indicadores fundamentalistas disponíveis para este ticker.")
        return

    cols = st.columns(len(indicadores_validos))
    for i, (nome, valor) in enumerate(indicadores_validos.items()):
        with cols[i]:
            if "Yield" in nome or "ROE" in nome:
                st.metric(label=nome, value=f"{valor*100:.2f}%")
            else:
                st.metric(label=nome, value=f"{valor:.2f}")

def exibir_grafico_precos_interativo(historico_completo: pd.DataFrame, ticker_symbol: str):
    """Exibe um gráfico de preços com seletor de período, usando dados já carregados."""
    display_ticker = ticker_symbol.replace(".SA", "")
    st.subheader(f"Histórico de Preços - {display_ticker}")
    
    periodos = ["1M", "6M", "1A", "5A", "Máx"]
    periodo_selecionado = st.radio("Selecione o Período:", periodos, horizontal=True, index=2, key=f"periodo_{ticker_symbol}")
    
    if historico_completo.empty:
        st.warning("Não há dados de histórico de preços para exibir.")
        return

    hoje = pd.to_datetime('today').tz_localize(None)
    historico_completo.index = historico_completo.index.tz_localize(None)

    if periodo_selecionado == "1M":
        historico_filtrado = historico_completo[historico_completo.index > hoje - pd.DateOffset(months=1)]
    elif periodo_selecionado == "6M":
        historico_filtrado = historico_completo[historico_completo.index > hoje - pd.DateOffset(months=6)]
    elif periodo_selecionado == "1A":
        historico_filtrado = historico_completo[historico_completo.index > hoje - pd.DateOffset(years=1)]
    elif periodo_selecionado == "5A":
        historico_filtrado = historico_completo[historico_completo.index > hoje - pd.DateOffset(years=5)]
    else: # "Máx"
        historico_filtrado = historico_completo
        
    st.line_chart(historico_filtrado['Close'])

def exibir_grafico_dividendos(dados_acao: dict):
    """Exibe um gráfico com o histórico de dividendos, se houver."""
    dividendos = dados_acao.get('dividendos')
    ticker_symbol = dados_acao.get('symbol', '')
    if dividendos is not None and not dividendos.empty:
        display_ticker = ticker_symbol.replace(".SA", "")
        st.subheader(f"Histórico de Dividendos - {display_ticker}")
        st.bar_chart(dividendos)

@st.cache_data(ttl=900) # Cache de 15 minutos para dados mais voláteis
def obter_analise_tecnica_tradingview(ticker: str):
    """Obtém a análise técnica resumida do TradingView."""
    try:
        # O TradingView para ativos brasileiros não usa '.SA' e precisa da exchange
        symbol = ticker.replace('.SA', '')
        exchange = "BMFBOVESPA"
        screener = "brazil"

        # Para tickers não brasileiros, a lógica pode precisar de ajuste (ex: EUA)
        if not ticker.endswith('.SA'):
            exchange = "NASDAQ" # Pode ser 'NYSE', 'AMEX', etc.
            screener = "america"

        handler = TA_Handler(
            symbol=symbol,
            screener=screener,
            exchange=exchange,
            interval=Interval.INTERVAL_1_DAY # Análise diária
        )
        
        analysis = handler.get_analysis()
        return {
            "recomendacao": analysis.summary.get('RECOMMENDATION'),
            "contadores": analysis.summary
        }
    except Exception as e:
        return {"erro": "Dados indisponíveis no momento, tente novamente mais tarde."}

@st.cache_data(ttl=3600) # Cache for 1 hour
def filtrar_acoes_por_criterios_teva():
    """
    Busca e filtra ações brasileiras com base nos critérios de elegibilidade
    do índice Teva Ações Fundamentos.
    """
    eligible_stocks = []
    exclusion_reasons = {} # To store reasons why a stock was excluded

    # Lista de tickers a serem considerados, baseada na composição do AUVG11, em ordem.
    brazilian_tickers_to_check = [
        "ITUB4.SA", "BBDC4.SA", "SBSP3.SA", "B3SA3.SA", "ITSA4.SA", "WEGE3.SA",
        "BBAS3.SA", "ABEV3.SA", "BPAC11.SA", "PRIO3.SA", "BBSE3.SA", "TOTS3.SA",
        "BBDC3.SA", "CMIG4.SA", "TIMS3.SA", "ITUB3.SA", "EGIE3.SA", "ISAE4.SA",
        "CMIN3.SA", "CPFE3.SA", "SAPR11.SA", "CXSE3.SA", "CYRE3.SA", "POMO4.SA",
        "CSMG3.SA", "DIRR3.SA", "CURY3.SA", "ODPV3.SA", "UNIP6.SA", "FRAS3.SA",
        "INTB3.SA", "ABCB4.SA", "LEVE3.SA", "SAPR4.SA"
    ]

    excluded_sectors = ["Retail", "Consumer Cyclical", "Consumer Defensive", "Restaurants", "Food & Beverage", "Airlines", "Transportation", "Travel Services"]

    today = datetime.now()
    
    for ticker in brazilian_tickers_to_check:
        reasons_for_exclusion_this_ticker = []
        try:
            dados_acao = obter_dados_acao(ticker)
            if "erro" in dados_acao:
                reasons_for_exclusion_this_ticker.append(f"Erro ao obter dados: {dados_acao['erro']}")
                exclusion_reasons[ticker] = reasons_for_exclusion_this_ticker
                continue

            info = dados_acao
            history_df = dados_acao['historico_precos']
            financials_annual = dados_acao['financials']
            balance_sheet_annual = dados_acao['balance_sheet']

            # --- CRITÉRIOS DE ELEGIBILIDADE ---

            # 1. TIPOS DE ATIVOS E LIQUIDEZ
            # 1.1 Listada por ao menos 5 anos (using pd.DateOffset for precision)
            if history_df.empty or history_df.index.min() > (today - pd.DateOffset(years=5)):
                reasons_for_exclusion_this_ticker.append("Não listada por 5 anos.")
                exclusion_reasons[ticker] = reasons_for_exclusion_this_ticker
                continue

            # 1.2 Volume mensal de negociação > R$ 100mm nos últimos 2 meses
            # 1.3 Negociação em 100% dos dias de negociação no mês anterior
            
            # Ensure history_df index is timezone-naive for comparison
            # This check is already done in obter_dados_acao for history_df, but good to re-confirm
            # Get data for the last 3 months to ensure we have 2 full months
            end_date_liquidez = today.date() # Use date only for comparison
            start_date_liquidez = (today - pd.DateOffset(months=3)).date() # ~3 months
            
            # Ensure history_df index is timezone-naive for comparison
            if history_df.index.tz is not None:
                history_df.index = history_df.index.tz_localize(None)

            recent_history = history_df[(history_df.index.date >= start_date_liquidez) & (history_df.index.date <= end_date_liquidez)]
            
            if recent_history.empty:
                reasons_for_exclusion_this_ticker.append("Histórico recente vazio para liquidez.")
                exclusion_reasons[ticker] = reasons_for_exclusion_this_ticker
                continue

            # Calculate daily traded value
            recent_history['TradedValue'] = recent_history['Volume'] * recent_history['Close']

            # Check for 100% trading days in the last full month
            last_full_month_end = (today.replace(day=1) - pd.DateOffset(days=1)).date()
            last_full_month_start = last_full_month_end.replace(day=1) # Start of the month
            
            trading_days_in_month = pd.bdate_range(start=last_full_month_start, end=last_full_month_end)
            actual_trading_days = recent_history[(recent_history.index.date >= last_full_month_start) & (recent_history.index.date <= last_full_month_end)].index.normalize().unique()
            
            if len(actual_trading_days) < len(trading_days_in_month):
                reasons_for_exclusion_this_ticker.append("Não negociou em 100% dos dias do último mês.")
                exclusion_reasons[ticker] = reasons_for_exclusion_this_ticker
                continue

            # Check monthly volume for the last two full months
            # Get the last two full months
            second_last_month_end = last_full_month_start - pd.DateOffset(days=1)
            second_last_month_start = second_last_month_end.replace(day=1)

            monthly_volumes = {}
            for month_start, month_end in [(last_full_month_start, last_full_month_end), (second_last_month_start, second_last_month_end)]:
                month_data = recent_history[(recent_history.index.date >= month_start) & (recent_history.index.date <= month_end)]
                if not month_data.empty:
                    # Ensure 'TradedValue' column exists before summing
                    if 'TradedValue' in month_data.columns:
                        monthly_volumes[month_start.strftime('%Y-%m')] = month_data['TradedValue'].sum()
                    else:
                        monthly_volumes[month_start.strftime('%Y-%m')] = 0 # No traded value data
                else:
                    monthly_volumes[month_start.strftime('%Y-%m')] = 0

            if len(monthly_volumes) < 2 or any(vol < 100_000_000 for vol in monthly_volumes.values()):
                reasons_for_exclusion_this_ticker.append("Volume mensal insuficiente nos últimos 2 meses.")
                exclusion_reasons[ticker] = reasons_for_exclusion_this_ticker
                continue

            # 2. CAPITALIZAÇÃO DE MERCADO E FREE FLOAT
            market_cap = info.get('marketCap')
            float_shares = info.get('floatShares')
            shares_outstanding = info.get('sharesOutstanding')
            
            if market_cap is None or market_cap < 3_000_000_000:
                reasons_for_exclusion_this_ticker.append("Capitalização de mercado inferior a R$ 3bi.")
                exclusion_reasons[ticker] = reasons_for_exclusion_this_ticker
                continue
            
            # Common issue with yfinance data for non-US stocks
            if float_shares is None or shares_outstanding is None or shares_outstanding == 0 or pd.isna(float_shares) or pd.isna(shares_outstanding):
                reasons_for_exclusion_this_ticker.append("Dados de free float ausentes ou inválidos.")
                exclusion_reasons[ticker] = reasons_for_exclusion_this_ticker
                continue

            free_float_percent = (float_shares / shares_outstanding) * 100
            if free_float_percent < 15:
                reasons_for_exclusion_this_ticker.append("Free float inferior a 15%.")
                exclusion_reasons[ticker] = reasons_for_exclusion_this_ticker
                continue

            # 3. CLASSIFICAÇÃO SETORIAL
            sector = info.get('sector')
            if sector and any(ex_sector in sector for ex_sector in excluded_sectors):
                reasons_for_exclusion_this_ticker.append(f"Setor excluído: {sector}.")
                exclusion_reasons[ticker] = reasons_for_exclusion_this_ticker
                continue

            # 4. QUALIDADE (Não implementável via yfinance de forma confiável)
            # "São inelegíveis empresas inadimplentes da entrega dos informes periódicos regulatórios.
            # Também são inelegíveis empresas em recuperação judicial ou extrajudicial."
            # This requires specific data feeds not available via yfinance. Skipping this check.
            # reasons_for_exclusion_this_ticker.append("Critério de Qualidade (Regulatório/Recuperação Judicial) não verificável via yfinance.")

            # 5. FUNDAMENTOS
            # 5.1 Lucro Líquido positivo nos 5 anos anteriores
            if financials_annual.empty or financials_annual.index.empty or 'Net Income' not in financials_annual.index:
                reasons_for_exclusion_this_ticker.append("Dados financeiros anuais ausentes ou sem Lucro Líquido.")
                exclusion_reasons[ticker] = reasons_for_exclusion_this_ticker
                continue
            
            net_income_series = financials_annual.loc['Net Income']
            # Ensure net_income_series is a Series of values, not a DataFrame or single value
            if isinstance(net_income_series, pd.DataFrame):
                if not net_income_series.empty:
                    net_income_series = net_income_series.iloc[0] # Assuming it's a single row DataFrame
                else:
                    reasons_for_exclusion_this_ticker.append("Lucro Líquido anual vazio após seleção.")
                    exclusion_reasons[ticker] = reasons_for_exclusion_this_ticker
                    continue
            elif not isinstance(net_income_series, pd.Series): # If it's a single value, convert to Series
                net_income_series = pd.Series([net_income_series])

            net_income_5y = net_income_series.head(5) # Get last 5 annual net incomes
            
            if net_income_5y.empty or any(pd.isna(ni) or ni <= 0 for ni in net_income_5y):
                reasons_for_exclusion_this_ticker.append("Lucro Líquido não positivo nos últimos 5 anos ou dados ausentes.")
                exclusion_reasons[ticker] = reasons_for_exclusion_this_ticker
                continue

            # 5.2 Endividamento Líquido / EBIT < 3x (não para Bancos e Seguradoras)
            is_bank_or_insurance = (sector and ("Financial Services" in sector or "Banks" in sector or "Insurance" in sector))
            
            if not is_bank_or_insurance:
                total_debt = None
                if 'Total Debt' in balance_sheet_annual.index and not balance_sheet_annual.empty:
                    series = balance_sheet_annual.loc['Total Debt']
                    if not series.empty:
                        total_debt = series.iloc[0]

                cash_equivalents = None
                if 'Cash And Cash Equivalents' in balance_sheet_annual.index and not balance_sheet_annual.empty:
                    series = balance_sheet_annual.loc['Cash And Cash Equivalents']
                    if not series.empty:
                        cash_equivalents = series.iloc[0]

                ebit = None
                if 'EBIT' in financials_annual.index and not financials_annual.empty:
                    series = financials_annual.loc['EBIT']
                    if not series.empty:
                        ebit = series.iloc[0]

                if total_debt is None or cash_equivalents is None or ebit is None or ebit <= 0:
                    reasons_for_exclusion_this_ticker.append("Dados para Endividamento Líquido/EBIT ausentes ou EBIT negativo.")
                    exclusion_reasons[ticker] = reasons_for_exclusion_this_ticker
                    continue
                
                net_debt = total_debt - cash_equivalents
                if net_debt / ebit >= 3:
                    reasons_for_exclusion_this_ticker.append("Endividamento Líquido/EBIT >= 3x.")
                    exclusion_reasons[ticker] = reasons_for_exclusion_this_ticker
                    continue

            # 5.3 ROE > 10%
            # Using LTM ROE from info, if available. Otherwise, calculate from annual.
            roe = info.get('returnOnEquity') # This is often LTM ROE
            if roe is None:
                net_income_ltm = info.get('netIncomeToCommon') # LTM Net Income (from info)
                total_equity = None
                if 'Total Stockholder Equity' in balance_sheet_annual.index and not balance_sheet_annual.empty:
                    series = balance_sheet_annual.loc['Total Stockholder Equity']
                    if not series.empty:
                        total_equity = series.iloc[0]

                if net_income_ltm is not None and total_equity is not None and total_equity > 0:
                    roe = net_income_ltm / total_equity
                
            if roe is None or pd.isna(roe) or roe < 0.10: # 10% (Added pd.isna check)
                reasons_for_exclusion_this_ticker.append("ROE inferior a 10% ou dados ausentes.")
                exclusion_reasons[ticker] = reasons_for_exclusion_this_ticker
                continue

            # 5.4 Margem Líquida > 8%
            net_income_ltm = info.get('netIncomeToCommon')
            total_revenue_ltm = info.get('totalRevenue')
            
            if pd.isna(net_income_ltm) or pd.isna(total_revenue_ltm) or total_revenue_ltm <= 0:
                reasons_for_exclusion_this_ticker.append("Dados para Margem Líquida ausentes ou Receita Líquida negativa.")
                exclusion_reasons[ticker] = reasons_for_exclusion_this_ticker
                continue
            
            net_margin = net_income_ltm / total_revenue_ltm
            if net_margin < 0.08: # 8%
                reasons_for_exclusion_this_ticker.append("Margem Líquida inferior a 8%.")
                exclusion_reasons[ticker] = reasons_for_exclusion_this_ticker
                continue

            # If all criteria pass
            eligible_stocks.append({
                "ticker": ticker,
                "longName": info.get('longName') or ticker.replace('.SA', ''),
                "currentPrice": info.get('currentPrice') or info.get('regularMarketPrice'),
                "marketCap": market_cap,
                "freeFloatPercent": free_float_percent,
                "sector": sector,
                "netIncome5Y": net_income_5y.tolist(), # Ensure it's a list for display
                "roe": roe,
                "netMargin": net_margin
            })

        except Exception as e:
            reasons_for_exclusion_this_ticker.append(f"Erro inesperado durante a análise: {e}")
            exclusion_reasons[ticker] = reasons_for_exclusion_this_ticker
            continue
            
    # A ordenação por capitalização de mercado foi removida para manter a ordem da lista original.
    # eligible_stocks.sort(key=lambda x: x.get('marketCap', 0), reverse=True)
    return eligible_stocks, exclusion_reasons # Return both the list and the reasons

def iniciar_analise():
    """Pega o valor do widget de input, valida e o define como o ticker a ser analisado."""
    ticker_input = st.session_state.get("ticker_input_key", "").strip().upper()
    
    st.session_state.ticker_analisado = ""
    st.session_state.ticker_foi_ajustado = False
    if 'input_error' in st.session_state:
        del st.session_state.input_error

    ticker_para_analise = ticker_input
    if ticker_input:
        if ticker_input[-1].isdigit() and '.' not in ticker_input:
            ticker_para_analise = f"{ticker_input}.SA"
            st.session_state.ticker_foi_ajustado = True

    st.session_state.ticker_analisado = ticker_para_analise

# --- Funções de Usuário ---
USERS_FILE = "usuarios.csv"

def salvar_usuario(email, senha, nome, data_nascimento):
    if not os.path.exists(USERS_FILE):
        df = pd.DataFrame(columns=["email", "senha", "nome", "data_nascimento"])
        df.to_csv(USERS_FILE, index=False)
    df = pd.read_csv(USERS_FILE)
    if email in df["email"].values:
        return False
    # Gera hash seguro da senha
    senha_hash = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()
    novo_usuario = pd.DataFrame([{"email": email, "senha": senha_hash, "nome": nome, "data_nascimento": data_nascimento}])
    df = pd.concat([df, novo_usuario], ignore_index=True)
    df.to_csv(USERS_FILE, index=False)
    return True

def autenticar_usuario(email, senha):
    if not os.path.exists(USERS_FILE):
        return False
    df = pd.read_csv(USERS_FILE)
    usuario_data = df[df["email"] == email]
    if usuario_data.empty:
        return False

    senha_hash_salva = usuario_data.iloc[0]["senha"]

    # Garante que a senha salva é uma string válida antes de tentar decodificar
    if not isinstance(senha_hash_salva, str):
        return False

    try:
        return bcrypt.checkpw(senha.encode('utf-8'), senha_hash_salva.encode('utf-8'))
    except ValueError:
        # Se o hash salvo for inválido (ex: senha antiga em texto plano), o login falha.
        return False

def obter_dados_usuario(email):
    if not os.path.exists(USERS_FILE):
        return None
    df = pd.read_csv(USERS_FILE)
    usuario_data = df[df["email"] == email]
    if not usuario_data.empty:
        return usuario_data.iloc[0]
    return None

def enviar_email(destinatario, assunto, corpo):
    try:
        import os
        remetente = os.getenv("SENDER_EMAIL") or st.secrets.get("email_credentials", {}).get("sender_email")
        senha_email = os.getenv("SENDER_PASSWORD") or st.secrets.get("email_credentials", {}).get("sender_password")
        
        if not remetente or not senha_email:
            return False, "Credenciais de email não configuradas"

        msg = MIMEText(corpo)
        msg["Subject"] = assunto
        msg["From"] = remetente
        msg["To"] = destinatario

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(remetente, senha_email)
            server.sendmail(remetente, destinatario, msg.as_string())
        return True, None
    except Exception as e:
        st.error("Dados indisponíveis no momento, tente novamente mais tarde.")
        return False, str(e)

def pagina_login():
    st.subheader("Login")
    with st.form("form_login"):
        email = st.text_input("E-mail", key="login_email", autocomplete="email")
        senha = st.text_input("Senha", type="password", key="login_senha", autocomplete="current-password")
        submit = st.form_submit_button("Entrar", use_container_width=True)
        if submit:
            if autenticar_usuario(email, senha):
                dados_usuario = obter_dados_usuario(email)
                st.session_state.usuario_logado = dados_usuario
                st.rerun()
            else:
                st.error("E-mail ou senha incorretos.")

    if st.button("Criar nova conta"):
        st.session_state.auth_page = "criar_conta"
        st.rerun()
    if st.button("Esqueci minha senha"):
        st.session_state.auth_page = "recuperar_senha"
        st.rerun()

def pagina_criar_conta():
    st.subheader("Criar Nova Conta")
    with st.form("form_criar_conta", clear_on_submit=True):
        nome = st.text_input("Nome Completo", key="create_name")
        data_nascimento = st.date_input("Data de Nascimento", min_value=datetime(1920, 1, 1), max_value=datetime.now(), key="create_dob", format="DD/MM/YYYY")
        email = st.text_input("E-mail", key="create_email")
        senha = st.text_input("Senha", type="password", key="create_password")
        senha2 = st.text_input("Confirme a senha", type="password", key="create_confirm_password")
        submit = st.form_submit_button("Criar Conta", use_container_width=True)
        if submit:
            hoje = datetime.now().date()
            idade = hoje.year - data_nascimento.year - ((hoje.month, hoje.day) < (data_nascimento.month, data_nascimento.day))

            if not nome or not email or not senha:
                st.error("Preencha todos os campos.")
            elif idade < 18:
                st.error("Você deve ter 18 anos ou mais para criar uma conta.")
            elif senha != senha2:
                st.error("As senhas não coincidem.")
            elif salvar_usuario(email, senha, nome, data_nascimento):
                st.toast("Conta criada com sucesso! Faça seu login.", icon="✅")
                st.session_state.auth_page = "login"
                st.rerun()
            else:
                st.warning("Este e-mail já está cadastrado.")
    if st.button("Voltar para Login"):
        st.session_state.auth_page = "login"
        st.rerun()

def pagina_recuperar_senha():
    st.subheader("Recuperar Senha")
    email = st.text_input("Digite seu e-mail para recuperar a senha", key="recuperar_email")
    if st.button("Enviar E-mail de Recuperação", use_container_width=True):
        if not os.path.exists(USERS_FILE):
            st.error("Nenhuma conta cadastrada no sistema.")
            return
        df = pd.read_csv(USERS_FILE)
        if email in df["email"].values:
            # NUNCA envie a senha ou o hash por e-mail.
            corpo_email = "Olá,\n\nRecebemos uma solicitação de recuperação de senha para sua conta. Se foi você, por favor, tente fazer login novamente ou crie uma nova conta se necessário.\n\nPor motivos de segurança, nunca enviamos senhas por e-mail.\n\nAtenciosamente,\nSua Ferramenta de Análise Fundamentalista"
            sucesso, erro = enviar_email(destinatario=email, assunto="Recuperação de Senha - Análise Fundamentalista", corpo=corpo_email)
            if sucesso:
                st.success("Um e-mail com instruções foi enviado para sua caixa de entrada.")
            # A mensagem de erro já é tratada dentro de enviar_email, ou será a genérica.
        else:
            st.error("E-mail não encontrado em nossa base de dados.")
    if st.button("Voltar para Login"):
        st.session_state.auth_page = "login"
        st.rerun()

# --- Funções da Carteira ---
CARTEIRA_FILE = "carteira.csv"

def adicionar_ativo_carteira(email_usuario, ticker, quantidade, preco_compra, data_compra, tipo):
    """Adiciona um novo ativo ao arquivo CSV da carteira."""
    if not os.path.exists(CARTEIRA_FILE):
        df = pd.DataFrame(columns=["email_usuario", "ticker", "quantidade", "preco_compra", "data_compra", "tipo"])
        df.to_csv(CARTEIRA_FILE, index=False)
    
    df = pd.read_csv(CARTEIRA_FILE)
    
    novo_ativo = pd.DataFrame([{"email_usuario": email_usuario, "ticker": ticker, "quantidade": quantidade, "preco_compra": preco_compra, "data_compra": data_compra.strftime('%Y-%m-%d'), "tipo": tipo}])
    df = pd.concat([df, novo_ativo], ignore_index=True)
    df.to_csv(CARTEIRA_FILE, index=False)
    return True

def carregar_carteira_usuario(email_usuario):
    """Carrega os ativos da carteira de um usuário específico."""
    if not os.path.exists(CARTEIRA_FILE):
        return pd.DataFrame()
    
    df = pd.read_csv(CARTEIRA_FILE, parse_dates=['data_compra'])
    return df[df["email_usuario"] == email_usuario].copy()

def atualizar_ativo_carteira(email_usuario, index_transacao, ticker, quantidade, preco_compra, data_compra, tipo):
    """Atualiza uma transação existente no arquivo CSV da carteira."""
    if not os.path.exists(CARTEIRA_FILE):
        return False
    
    df = pd.read_csv(CARTEIRA_FILE)
    
    if index_transacao not in df[df['email_usuario'] == email_usuario].index:
        st.error("Erro: Tentativa de editar uma transação inválida.")
        return False

    df.loc[index_transacao, 'ticker'] = ticker
    df.loc[index_transacao, 'quantidade'] = quantidade
    df.loc[index_transacao, 'preco_compra'] = preco_compra
    df.loc[index_transacao, 'data_compra'] = data_compra.strftime('%Y-%m-%d')
    df.loc[index_transacao, 'tipo'] = tipo
    
    df.to_csv(CARTEIRA_FILE, index=False)
    return True

def remover_ativo_carteira(email_usuario, index_transacao):
    """Remove uma transação do arquivo CSV da carteira."""
    if not os.path.exists(CARTEIRA_FILE):
        return False
        
    df = pd.read_csv(CARTEIRA_FILE)

    if index_transacao not in df[df['email_usuario'] == email_usuario].index:
        st.error("Erro: Tentativa de remover uma transação inválida.")
        return False

    df.drop(index_transacao, inplace=True)
    df.to_csv(CARTEIRA_FILE, index=False)
    return True

@st.cache_data(ttl=86400)
def obter_info_empresa(ticker):
    """Obtém os dados de 'info' de uma empresa e os armazena em cache."""
    try:
        return yf.Ticker(ticker).info
    except Exception:
        return {}

@st.cache_data(ttl=3600)
def obter_noticias_ativos(tickers: list):
    """Busca notícias para uma lista de tickers usando o RSS do Google News."""
    noticias_por_ticker = {}
    for ticker in tickers:
        try:
            # Remove .SA para uma busca mais eficaz no Google News Brasil
            search_term = ticker.replace('.SA', '')
            rss_url = f"https://news.google.com/rss/search?q={search_term}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
            
            feed = feedparser.parse(rss_url)
            
            if feed.entries:
                lista_noticias = []
                for entry in feed.entries[:3]: # Pega as 3 primeiras notícias
                    # Converte o 'published_parsed' (time.struct_time) para um timestamp Unix
                    published_timestamp = time.mktime(entry.published_parsed) if hasattr(entry, 'published_parsed') else None
                    
                    noticia = {
                        'title': entry.title,
                        'link': entry.link,
                        'publisher': entry.get('source', {}).get('title', 'Google News'),
                        'providerPublishTime': published_timestamp
                    }
                    lista_noticias.append(noticia)
                
                noticias_por_ticker[ticker] = lista_noticias
        except Exception:
            # Em caso de falha para um ticker, continua para o próximo
            continue
    return noticias_por_ticker

@st.cache_data(ttl=86400) # Cache CDI data for a day
def obter_dados_cdi(start_date, end_date):
    """
    Busca a série histórica do CDI no webservice do Banco Central.
    Retorna uma série normalizada (base 100).
    """
    try:
        # Formata as datas para a API do BCB
        start_str = pd.to_datetime(start_date).strftime('%d/%m/%Y')
        end_str = pd.to_datetime(end_date).strftime('%d/%m/%Y')

        # Código da série do CDI diário no SGS é 12
        url = f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados?formato=json&dataInicial={start_str}&dataFinal={end_str}"

        # Adiciona o header 'Accept' para evitar o erro 406 Not Acceptable da API do BCB
        headers = {'Accept': 'application/json'}
        # Adiciona um timeout para evitar que a aplicação trave e melhora o feedback
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status() # Lança um erro para status HTTP 4xx/5xx

        data = response.json()
        if not data:
            st.warning(f"A API do Banco Central não retornou dados do CDI para o período de {start_str} a {end_str}.")
            return pd.Series(dtype=float)

        cdi_df = pd.DataFrame(data)
        cdi_df['data'] = pd.to_datetime(cdi_df['data'], format='%d/%m/%Y')
        cdi_df['valor'] = pd.to_numeric(cdi_df['valor'])

        cdi_df.set_index('data', inplace=True)

        # A API retorna a taxa percentual ao dia. Precisamos converter para fator.
        cdi_df['fator'] = 1 + (cdi_df['valor'] / 100)

        # Calcula o acumulado
        cdi_acumulado = cdi_df['fator'].cumprod()

        # Normaliza para base 100
        return 100 * (cdi_acumulado / cdi_acumulado.iloc[0])
    except requests.exceptions.RequestException as e:
        st.warning(f"Falha na comunicação com a API do Banco Central para obter o CDI. Verifique sua conexão ou tente mais tarde. Erro: {e}")
        return pd.Series(dtype=float)
    except Exception as e:
        st.warning(f"Ocorreu um erro inesperado ao processar os dados do CDI. Erro: {e}")
        return pd.Series(dtype=float)

def gerar_grafico_evolucao_patrimonio(email_usuario, indices_selecionados=None):
    """Gera um gráfico de linha mostrando a evolução do patrimônio da carteira.
    Retorna o objeto Plotly Figure ou None em caso de erro/dados vazios.
    """
    transacoes_df = carregar_carteira_usuario(email_usuario)
    if transacoes_df.empty:
        return None

    if indices_selecionados is None:
        indices_selecionados = []

    # Garante que 'data_compra' é datetime e timezone-naive
    transacoes_df['data_compra'] = pd.to_datetime(transacoes_df['data_compra']).dt.normalize()
    transacoes_df.sort_values(by='data_compra', inplace=True)
    
    if 'tipo' not in transacoes_df.columns:
        transacoes_df['tipo'] = 'Compra'
    transacoes_df['tipo'].fillna('Compra', inplace=True)

    start_date = transacoes_df['data_compra'].min()
    end_date = pd.to_datetime('today').normalize()
    tickers = transacoes_df['ticker'].unique().tolist()
    
    if start_date > end_date:
        # st.warning("Data de transação futura detectada. Gráfico de evolução não pode ser gerado.")
        return None

    try:
        # Baixa os dados de preços para todos os tickers no período
        precos_hist_raw = yf.download(tickers, start=start_date, end=end_date, progress=False)
        if precos_hist_raw.empty:
            # st.warning("Não há dados de preços históricos para os tickers no período selecionado.")
            return None
        
        # Garante que o índice de datas seja timezone-naive para evitar erros de comparação
        if precos_hist_raw.index.tz is not None:
            precos_hist_raw.index = precos_hist_raw.index.tz_localize(None)
        precos_hist_raw.index = precos_hist_raw.index.normalize() # Normaliza para apenas a data

        # Tenta usar 'Adj Close', se não disponível, tenta 'Close'
        precos_hist = None
        if 'Adj Close' in precos_hist_raw.columns:
            precos_hist = precos_hist_raw['Adj Close']
        elif 'Close' in precos_hist_raw.columns:
            precos_hist = precos_hist_raw['Close']
        
        if precos_hist is None or precos_hist.empty:
            # st.warning("Não há dados de preços ajustados ou de fechamento para os tickers no período selecionado.")
            return None

        # Se for um único ticker, precos_hist será uma Series, converte para DataFrame
        if isinstance(precos_hist, pd.Series):
            precos_hist = precos_hist.to_frame(name=tickers[0])

        # Preenche valores ausentes (feriados, fins de semana) com o último preço válido
        precos_hist.ffill(inplace=True)

        datas_range = pd.date_range(start=start_date, end=end_date)
        posicao_df = pd.DataFrame(0.0, index=datas_range, columns=tickers)
        custo_acumulado_df = pd.DataFrame(0.0, index=datas_range, columns=tickers)

        custo_total_ticker = {ticker: 0.0 for ticker in tickers}
        qtd_total_ticker = {ticker: 0.0 for ticker in tickers}

        for index, row in transacoes_df.iterrows():
            data_transacao, ticker = row['data_compra'], row['ticker']
            quantidade, preco = row['quantidade'], row['preco_compra']
            
            if row['tipo'] == 'Compra':
                posicao_df.loc[data_transacao:, ticker] += quantidade
                custo_a_adicionar = quantidade * preco
                custo_acumulado_df.loc[data_transacao:, ticker] += custo_a_adicionar
                
                custo_total_ticker[ticker] += custo_a_adicionar
                qtd_total_ticker[ticker] += quantidade
            else: # Venda
                preco_medio_antes_venda = (custo_total_ticker[ticker] / qtd_total_ticker[ticker]) if qtd_total_ticker[ticker] > 0 else 0
                custo_a_remover = quantidade * preco_medio_antes_venda
                
                posicao_df.loc[data_transacao:, ticker] -= quantidade
                custo_acumulado_df.loc[data_transacao:, ticker] -= custo_a_remover

                custo_total_ticker[ticker] -= custo_a_remover
                qtd_total_ticker[ticker] -= quantidade

        posicao_df = posicao_df.reindex(precos_hist.index, method='ffill').fillna(0)
        custo_acumulado_df = custo_acumulado_df.reindex(precos_hist.index, method='ffill').fillna(0)

        patrimonio_df = posicao_df * precos_hist
        evolucao_df = pd.DataFrame({'Patrimônio': patrimonio_df.sum(axis=1), 'Custo Total': custo_acumulado_df.sum(axis=1)})
        evolucao_df = evolucao_df[evolucao_df.sum(axis=1) > 0]

        if evolucao_df.empty: return None
        
        # --- Normalização e Comparação com Índices ---
        df_comparativo = pd.DataFrame(index=evolucao_df.index)
        
        # Normaliza o patrimônio da carteira
        patrimonio_series = evolucao_df['Patrimônio']
        df_comparativo['Minha Carteira'] = 100 * (patrimonio_series / patrimonio_series.iloc[0])

        # Normaliza o custo total, se houver
        custo_series = evolucao_df['Custo Total']
        if not custo_series.empty and custo_series.iloc[0] > 0:
            df_comparativo['Custo Total (Normalizado)'] = 100 * (custo_series / custo_series.iloc[0])

        INDICES_TICKERS = {
            "IBOV": "^BVSP",
            "S&P 500 (SPX)": "^GSPC",
            "SMLL": "SMAL11.SA",
            "IDIV": "IDIV11.SA",
            "IVVB11": "IVVB11.SA"
        }

        for indice in indices_selecionados:
            try:
                if indice == "CDI":
                    dados_indice = obter_dados_cdi(start_date, end_date)
                    if not dados_indice.empty:
                        df_comparativo[indice] = dados_indice.reindex(df_comparativo.index, method='ffill')
                else:
                    ticker = INDICES_TICKERS.get(indice)
                    if not ticker: continue
                    
                    dados_completo = yf.download(ticker, start=start_date, end=end_date, progress=False)
                    
                    if dados_completo.empty:
                        st.warning(f"Não foi possível carregar dados históricos para o índice {indice} ({ticker}). Verifique o período selecionado ou a disponibilidade do ticker.")
                        continue

                    dados_indice_series = None
                    if 'Adj Close' in dados_completo.columns:
                        dados_indice_series = dados_completo['Adj Close']
                    elif 'Close' in dados_completo.columns: # Fallback to 'Close' if 'Adj Close' is not there
                        dados_indice_series = dados_completo['Close']
                    
                    # If yfinance returned a DataFrame (e.g., with multi-index columns for a single ticker),
                    # we select the first column to ensure we have a Series.
                    if isinstance(dados_indice_series, pd.DataFrame):
                        if not dados_indice_series.empty:
                            dados_indice_series = dados_indice_series.iloc[:, 0]
                        else:
                            dados_indice_series = pd.Series(dtype=float) # Treat empty DataFrame as empty Series

                    if dados_indice_series is None or dados_indice_series.empty:
                        st.warning(f"Não foi possível encontrar dados de fechamento (Adj Close ou Close) para o índice {indice} ({ticker}).")
                        continue

                    if dados_indice_series.index.tz is not None:
                        dados_indice_series.index = dados_indice_series.index.tz_localize(None)
                    
                    # Ensure the series is aligned with the main portfolio history index
                    dados_indice_reindexed = dados_indice_series.reindex(df_comparativo.index, method='ffill').bfill()
                    
                    if dados_indice_reindexed.empty or dados_indice_reindexed.iloc[0] == 0 or pd.isna(dados_indice_reindexed.iloc[0]):
                        st.warning(f"Dados insuficientes ou inválidos para normalizar o índice {indice} ({ticker}).")
                        continue

                    df_comparativo[indice] = 100 * (dados_indice_reindexed / dados_indice_reindexed.iloc[0])
            except Exception as e:
                st.warning(f"Ocorreu um erro inesperado ao carregar os dados para o índice {indice}: {e}")

        fig = px.line(df_comparativo.dropna(how='all', axis=1), title="Rentabilidade da Carteira vs. Índices (Base 100)",
                      labels={"value": "Performance (Base 100)", "index": "Data", "variable": "Ativo"})
        fig.update_layout(hovermode="x unified")
        return fig
    except Exception as e:
        # Em caso de qualquer erro inesperado, retorna None para não quebrar a aplicação.
        # O erro agora é exibido para o usuário para facilitar a depuração.
        st.error(f"Ocorreu um erro crítico ao gerar o gráfico de evolução do patrimônio: {e}")
        return None

def verificar_e_enviar_alertas(email_usuario, dados_carteira):
    """Verifica se algum preço-alvo foi atingido e envia e-mail."""
    if dados_carteira.get("erro"): return

    alertas_df = carregar_alertas_usuario(email_usuario)
    alertas_ativos = alertas_df[alertas_df['status'] == 'ativo']
    if alertas_ativos.empty: return

    posicao_atual_df = dados_carteira.get("posicao_atual_df")
    if posicao_atual_df is None or posicao_atual_df.empty: return

    merged_df = pd.merge(alertas_ativos, posicao_atual_df[['ticker', 'Preço Atual']], on='ticker', how='inner')

    for index, row in merged_df.iterrows():
        if pd.notna(row['Preço Atual']) and row['Preço Atual'] >= row['preco_alvo']:
            ticker, preco_alvo, preco_atual = row['ticker'], row['preco_alvo'], row['Preço Atual']
            assunto = f"🔔 Alerta de Preço Atingido: {ticker}"
            corpo = f"Olá,\n\nSeu alerta de preço para o ativo {ticker} foi atingido.\n\nPreço Alvo: R$ {preco_alvo:,.2f}\nPreço Atual: R$ {preco_atual:,.2f}\n\nAtenciosamente,\nSua Ferramenta de Análise Fundamentalista"
            sucesso, erro_msg = enviar_email(email_usuario, assunto, corpo)
            if sucesso:
                st.toast(f"E-mail de alerta para {ticker} enviado!", icon="📧")
                df_geral = pd.read_csv(ALERTAS_FILE)
                df_geral.loc[(df_geral['email_usuario'] == email_usuario) & (df_geral['ticker'] == ticker), 'status'] = 'enviado'
                df_geral.to_csv(ALERTAS_FILE, index=False)

@st.cache_data(ttl=900) # Cache de 15 minutos
def obter_preco_atual_cached(ticker: str):
    """Obtém o preço de fechamento mais recente para um único ticker."""
    try:
        dados = yf.Ticker(ticker).history(period='5d', interval='1d')
        if dados.empty:
            return None
        
        # Tenta 'Adj Close', se não disponível ou NaN, tenta 'Close'
        preco = None
        if 'Adj Close' in dados.columns and not dados['Adj Close'].empty:
            preco = dados['Adj Close'].ffill().iloc[-1]
        if pd.isna(preco) and 'Close' in dados.columns and not dados['Close'].empty:
            preco = dados['Close'].ffill().iloc[-1]
        
        return preco if pd.notna(preco) else None
    except Exception:
        return None

@st.cache_data(ttl=86400) # Cache de 1 dia para dividendos
def obter_dividendos_historicos_cached(ticker: str):
    """Obtém o histórico de dividendos para um único ticker."""
    try:
        dividends_hist = yf.Ticker(ticker).dividends
        if dividends_hist.index.tz is not None:
            dividends_hist.index = dividends_hist.index.tz_localize(None)
        return dividends_hist
    except Exception:
        return pd.Series(dtype=float) # Retorna uma série vazia em caso de erro

def _consolidar_posicao_atual(carteira_df):
    """Consolida as transações para obter a posição atual e o preço médio."""
    compras_df = carteira_df[carteira_df['tipo'] == 'Compra'].copy()
    vendas_df = carteira_df[carteira_df['tipo'] == 'Venda'].copy()

    # Agrupar compras para calcular preço médio
    if not compras_df.empty:
        compras_df['Custo Total Individual'] = compras_df['quantidade'] * compras_df['preco_compra']
        compras_agrupadas = compras_df.groupby('ticker').agg(
            qtd_comprada=('quantidade', 'sum'),
            custo_total_compras=('Custo Total Individual', 'sum')
        ).reset_index()
        compras_agrupadas['preco_medio_ponderado'] = compras_agrupadas['custo_total_compras'] / compras_agrupadas['qtd_comprada']
    else:
        compras_agrupadas = pd.DataFrame(columns=['ticker', 'qtd_comprada', 'custo_total_compras', 'preco_medio_ponderado'])

    # Agrupar vendas
    if not vendas_df.empty:
        vendas_agrupadas = vendas_df.groupby('ticker').agg(
            qtd_vendida=('quantidade', 'sum')
        ).reset_index()
    else:
        vendas_agrupadas = pd.DataFrame(columns=['ticker', 'qtd_vendida'])

    # Merge para obter posição atual
    carteira_consolidada = pd.merge(compras_agrupadas, vendas_agrupadas, on='ticker', how='outer').fillna(0)
    carteira_consolidada['quantidade_atual'] = carteira_consolidada['qtd_comprada'] - carteira_consolidada['qtd_vendida']
    
    posicao_atual_df = carteira_consolidada[carteira_consolidada['quantidade_atual'] > 0.00001].copy()
    
    return posicao_atual_df, compras_agrupadas

def _calcular_lucro_prejuizo_realizado(carteira_df, compras_agrupadas):
    """Calcula o lucro e prejuízo realizados a partir das vendas."""
    vendas_df = carteira_df[carteira_df['tipo'] == 'Venda'].copy()
    if vendas_df.empty or compras_agrupadas.empty:
        return 0.0, 0.0

    vendas_com_custo = pd.merge(
        vendas_df,
        compras_agrupadas[['ticker', 'preco_medio_ponderado']],
        on='ticker',
        how='inner'
    )
    
    if vendas_com_custo.empty:
        return 0.0, 0.0

    vendas_com_custo['lucro_realizado_individual'] = \
        (vendas_com_custo['quantidade'] * vendas_com_custo['preco_compra']) - \
        (vendas_com_custo['quantidade'] * vendas_com_custo['preco_medio_ponderado'])

    lucros = vendas_com_custo[vendas_com_custo['lucro_realizado_individual'] > 0]['lucro_realizado_individual'].sum()
    prejuizos = vendas_com_custo[vendas_com_custo['lucro_realizado_individual'] < 0]['lucro_realizado_individual'].sum()
    
    return lucros, prejuizos

def _calcular_dividendos_recebidos(carteira_df):
    """Calcula o total de dividendos recebidos para todos os ativos na carteira."""
    total_dividendos = 0.0
    all_tickers = carteira_df['ticker'].unique().tolist()

    for ticker in all_tickers:
        dividends_hist = obter_dividendos_historicos_cached(ticker)
        if dividends_hist.empty:
            continue

        transacoes_ticker = carteira_df[carteira_df['ticker'] == ticker].sort_values('data_compra')

        for div_date, div_amount in dividends_hist.items():
            div_date_naive = pd.to_datetime(div_date).tz_localize(None)
            
            transacoes_antes_div = transacoes_ticker[transacoes_ticker['data_compra'] < div_date_naive]
            
            if not transacoes_antes_div.empty:
                compras = transacoes_antes_div[transacoes_antes_div['tipo'] == 'Compra']['quantidade'].sum()
                vendas = transacoes_antes_div[transacoes_antes_div['tipo'] == 'Venda']['quantidade'].sum()
                shares_held = compras - vendas

                if shares_held > 0:
                    total_dividendos += shares_held * div_amount
    
    return total_dividendos

# --- Funções de Alertas ---
ALERTAS_FILE = "alertas.csv"

def carregar_alertas_usuario(email_usuario):
    """Carrega todos os alertas de um usuário."""
    if not os.path.exists(ALERTAS_FILE):
        return pd.DataFrame(columns=['email_usuario', 'ticker', 'preco_alvo', 'status'])
    df = pd.read_csv(ALERTAS_FILE)
    return df[df['email_usuario'] == email_usuario].copy()

def salvar_alerta(email_usuario, ticker, preco_alvo):
    """Salva ou atualiza um alerta de preço para um usuário e ticker."""
    if not os.path.exists(ALERTAS_FILE):
        df = pd.DataFrame(columns=['email_usuario', 'ticker', 'preco_alvo', 'status'])
    else:
        df = pd.read_csv(ALERTAS_FILE)

    filtro = (df['email_usuario'] == email_usuario) & (df['ticker'] == ticker)
    alerta_existente = df[filtro]

    if preco_alvo > 0:
        if not alerta_existente.empty:
            df.loc[alerta_existente.index[0], 'preco_alvo'] = preco_alvo
            df.loc[alerta_existente.index[0], 'status'] = 'ativo'
        else:
            novo_alerta = pd.DataFrame([{'email_usuario': email_usuario, 'ticker': ticker, 'preco_alvo': preco_alvo, 'status': 'ativo'}])
            df = pd.concat([df, novo_alerta], ignore_index=True)
        st.toast(f"Alerta para {ticker} salvo em R$ {preco_alvo:.2f}!", icon="🔔")
    else:
        if not alerta_existente.empty:
            df.drop(alerta_existente.index, inplace=True)
            st.toast(f"Alerta para {ticker} removido.", icon="🗑️")

    df.to_csv(ALERTAS_FILE, index=False)
    st.rerun()

def calcular_dados_carteira(email_usuario):
    """
    Calcula todas as métricas da carteira, orquestrando funções auxiliares para
    maior clareza e utilizando cache granular para otimização.
    """
    carteira_df = carregar_carteira_usuario(email_usuario)
    if carteira_df.empty:
        return {"erro": "Carteira vazia"}

    try:
        # --- 1. Preparação ---
        carteira_df['data_compra'] = pd.to_datetime(carteira_df['data_compra'])
        carteira_df = carteira_df[carteira_df['data_compra'] <= pd.to_datetime('today')].copy()
        if carteira_df.empty:
            return {"erro": "Nenhuma transação encontrada até a data de hoje."}
        if 'tipo' not in carteira_df.columns:
            carteira_df['tipo'] = 'Compra'
        carteira_df['tipo'].fillna('Compra', inplace=True)

        # --- 2. Cálculos Principais ---
        posicao_atual_df, compras_agrupadas = _consolidar_posicao_atual(carteira_df)
        lucros_realizados, prejuizos_realizados = _calcular_lucro_prejuizo_realizado(carteira_df, compras_agrupadas)
        total_dividendos = _calcular_dividendos_recebidos(carteira_df)

        # --- 3. Cálculos de Mercado (Cotação Atual) ---
        if not posicao_atual_df.empty:
            # Usa a função de cache granular para obter preços
            precos_atuais = {ticker: obter_preco_atual_cached(ticker) for ticker in posicao_atual_df['ticker']}
            posicao_atual_df['Preço Atual'] = posicao_atual_df['ticker'].map(precos_atuais)

            # Avisa sobre tickers sem preço
            ativos_sem_preco = posicao_atual_df[posicao_atual_df['Preço Atual'].isna()]
            if not ativos_sem_preco.empty:
                st.warning(f"Não foi possível obter a cotação atual para: {', '.join(ativos_sem_preco['ticker'].tolist())}. Estes ativos não serão considerados nos totais de mercado.")

            # Calcula valores baseados no preço atual
            posicao_atual_df['Custo Total Posição'] = posicao_atual_df['quantidade_atual'] * posicao_atual_df['preco_medio_ponderado']
            posicao_atual_df['Valor Atual'] = posicao_atual_df['quantidade_atual'] * posicao_atual_df['Preço Atual']
            posicao_atual_df['Lucro/Prejuízo Não Realizado'] = posicao_atual_df['Valor Atual'] - posicao_atual_df['Custo Total Posição']
            posicao_atual_df['Variação (%)'] = (posicao_atual_df['Lucro/Prejuízo Não Realizado'] / posicao_atual_df['Custo Total Posição'].replace(0, 1)) * 100
            
            total_investido = posicao_atual_df['Custo Total Posição'].sum()
            valor_atual_total = posicao_atual_df['Valor Atual'].sum()
            lucro_nao_realizado_total = posicao_atual_df['Lucro/Prejuízo Não Realizado'].sum()
        else:
            total_investido, valor_atual_total, lucro_nao_realizado_total = 0.0, 0.0, 0.0

        # --- 4. Retorno dos Dados ---
        return {
            "posicao_atual_df": posicao_atual_df,
            "total_investido": total_investido,
            "valor_atual_total": valor_atual_total,
            "lucro_nao_realizado_total": lucro_nao_realizado_total,
            "lucros_realizados_total": lucros_realizados,
            "prejuizos_realizados_total": prejuizos_realizados,
            "total_dividendos_recebidos": total_dividendos,
            "erro": None
        }
    except Exception as e:
        st.error(f"Erro ao calcular dados da carteira: {e}")
        return {"erro": "Dados indisponíveis no momento, tente novamente mais tarde."}

def pagina_carteira():
    """Renderiza a página da carteira do usuário."""
    st.header("💼 Minha Carteira de Ativos")
    email_usuario = st.session_state.usuario_logado['email']

    if 'editing_transaction_id' in st.session_state and st.session_state.editing_transaction_id is not None:
        transaction_id = st.session_state.editing_transaction_id
        try:
            full_carteira_df = pd.read_csv(CARTEIRA_FILE, keep_default_na=False)
            transaction_data = full_carteira_df.loc[transaction_id]
        except (FileNotFoundError, KeyError):
            st.error("Não foi possível encontrar a transação para editar.")
            del st.session_state.editing_transaction_id
            st.rerun()
            return

        st.subheader("📝 Editando Transação")
        with st.form("form_edit_ativo"):
            st.info(f"Editando a transação do ativo **{transaction_data['ticker']}** de {pd.to_datetime(transaction_data['data_compra']).strftime('%d/%m/%Y')}.")
            
            cols = st.columns([1, 2, 1, 1, 1])
            with cols[0]:
                tipo_transacao_edit = transaction_data.get('tipo', 'Compra')
                edit_tipo = st.selectbox("Tipo", ["Compra", "Venda"], index=["Compra", "Venda"].index(tipo_transacao_edit))
            with cols[1]:
                edit_ticker = st.text_input("Ticker", value=transaction_data['ticker']).upper()
            with cols[2]:
                edit_quantidade = st.number_input("Quantidade", value=float(transaction_data['quantidade']), min_value=0.00001, format="%.5f")
            with cols[3]:
                edit_preco = st.number_input("Preço (un.)", value=float(transaction_data['preco_compra']), min_value=0.01, format="%.2f")
            with cols[4]:
                edit_data = st.date_input("Data", value=pd.to_datetime(transaction_data['data_compra']), max_value=datetime.now(), format="DD/MM/YYYY")

            c1, c2 = st.columns(2)
            if c1.form_submit_button("Salvar Alterações", use_container_width=True, type="primary"):
                ticker_final = edit_ticker
                if '.' not in ticker_final and any(char.isdigit() for char in ticker_final):
                    ticker_final = f"{ticker_final}.SA"
                
   
                sucesso = atualizar_ativo_carteira(email_usuario, transaction_id, ticker_final, edit_quantidade, edit_preco, edit_data, edit_tipo)
                if sucesso:
                    st.toast("Transação atualizada com sucesso!", icon="✅")
                    del st.session_state.editing_transaction_id
                    st.rerun()
                else:
                    st.error("Falha ao atualizar a transação.")

            if c2.form_submit_button("Cancelar", use_container_width=True):
                del st.session_state.editing_transaction_id
                st.rerun()
        return

    with st.form("form_add_ativo", clear_on_submit=True):
        st.subheader("Adicionar Nova Transação")
        col1, col2, col3, col4, col5 = st.columns([1, 2, 1, 1, 1])
        with col1:
            tipo_transacao = st.selectbox("Tipo", ["Compra", "Venda"], key="tipo_transacao")
        with col2:
            ticker_input = st.text_input("Ticker", placeholder="Ex: PETR4, AAPL").upper()
        with col3:
            quantidade = st.number_input("Quantidade", min_value=0.00001, format="%.5f")
        with col4:
            preco_compra = st.number_input("Preço (un.)", min_value=0.01, format="%.2f")
        with col5:
            data_compra = st.date_input("Data", value=datetime.now(), max_value=datetime.now(), format="DD/MM/YYYY")
        
        if st.form_submit_button("Adicionar à Carteira", use_container_width=True):
            if ticker_input and quantidade > 0 and preco_compra > 0:
                ticker_final = ticker_input
                if '.' not in ticker_final and any(char.isdigit() for char in ticker_final):
                    ticker_final = f"{ticker_final}.SA"
                
                with st.spinner(f"Validando o ticker {ticker_final}..."):
                    dados_validacao = yf.Ticker(ticker_final).history(period="1d")
                
                if dados_validacao.empty:
                    st.error(f"O ticker '{ticker_final}' parece ser inválido ou não possui dados recentes.")
                else:
                    adicionar_ativo_carteira(email_usuario, ticker_final, quantidade, preco_compra, data_compra, tipo_transacao)
                    st.success(f"{tipo_transacao} de {ticker_final} adicionada com sucesso!")
                    st.rerun()
            else:
                st.error("Por favor, preencha todos os campos corretamente.")

    st.markdown("---")
    st.subheader("Composição Atual")
    
    with st.spinner("Atualizando dados da carteira..."):
        dados_carteira = calcular_dados_carteira(email_usuario)
        verificar_e_enviar_alertas(email_usuario, dados_carteira)

    if dados_carteira.get("erro") == "Carteira vazia":
        st.info("Sua carteira está vazia. Adicione uma transação acima.")
    elif dados_carteira.get("erro"):
        st.error(f"Ocorreu um erro ao carregar sua carteira: {dados_carteira['erro']}")
    else:
        c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
        with c1:
            st.markdown("<div style='text-align:center; margin-bottom:10px;'>Custo da Carteira</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align:center; font-size:1.3rem; font-weight:600; height:40px; display:flex; align-items:center; justify-content:center;'>R$ {dados_carteira['total_investido']:,.2f}</div>", unsafe_allow_html=True)
        with c2:
            st.markdown("<div style='text-align:center; margin-bottom:10px;'>Valor Atual</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align:center; font-size:1.3rem; font-weight:600; height:40px; display:flex; align-items:center; justify-content:center;'>R$ {dados_carteira['valor_atual_total']:,.2f}</div>", unsafe_allow_html=True)
        with c3:
            st.markdown("<div style='text-align:center; margin-bottom:10px;'>Lucro Não Realizado</div>", unsafe_allow_html=True)
            posicao_atual_df = dados_carteira.get('posicao_atual_df')
            if posicao_atual_df is not None and not posicao_atual_df.empty and 'Lucro/Prejuízo Não Realizado' in posicao_atual_df.columns:
                lucros_positivos = posicao_atual_df[posicao_atual_df['Lucro/Prejuízo Não Realizado'] > 0]['Lucro/Prejuízo Não Realizado'].sum()
                st.markdown(f"<div style='text-align:center; color:green; font-size:1.3rem; font-weight:600; height:40px; display:flex; align-items:center; justify-content:center;'>R$ {lucros_positivos:,.2f}</div>", unsafe_allow_html=True)
            else:
                st.markdown(f"<div style='text-align:center; font-size:1.3rem; font-weight:600; height:40px; display:flex; align-items:center; justify-content:center;'>R$ 0,00</div>", unsafe_allow_html=True)
        with c4:
            st.markdown("<div style='text-align:center; margin-bottom:10px;'>Prejuízo Não Realizado</div>", unsafe_allow_html=True)
            posicao_atual_df = dados_carteira.get('posicao_atual_df')
            if posicao_atual_df is not None and not posicao_atual_df.empty and 'Lucro/Prejuízo Não Realizado' in posicao_atual_df.columns:
                prejuizos_negativos = posicao_atual_df[posicao_atual_df['Lucro/Prejuízo Não Realizado'] < 0]['Lucro/Prejuízo Não Realizado'].sum()
                st.markdown(f"<div style='text-align:center; color:red; font-size:1.3rem; font-weight:600; height:40px; display:flex; align-items:center; justify-content:center;'>R$ {abs(prejuizos_negativos):,.2f}</div>", unsafe_allow_html=True)
            else:
                st.markdown(f"<div style='text-align:center; font-size:1.3rem; font-weight:600; height:40px; display:flex; align-items:center; justify-content:center;'>R$ 0,00</div>", unsafe_allow_html=True)
        with c5:
            st.markdown("Lucro Realizado")
            st.markdown(f"<div style='color:green; font-size:1.3rem; font-weight:600;'>R$ {dados_carteira['lucros_realizados_total']:,.2f}</div>", unsafe_allow_html=True)
        with c6:
            st.markdown("Prejuízo Realizado")
            st.markdown(f"<div style='color:red; font-size:1.3rem; font-weight:600;'>R$ {abs(dados_carteira['prejuizos_realizados_total']):,.2f}</div>", unsafe_allow_html=True)
        with c7:
            st.markdown("Dividendos Recebidos")
            st.markdown(f"<div style='color:blue; font-size:1.3rem; font-weight:600;'>R$ {dados_carteira['total_dividendos_recebidos']:,.2f}</div>", unsafe_allow_html=True)

        posicao_atual_df = dados_carteira['posicao_atual_df']
        if not posicao_atual_df.empty:
            st.subheader("Distribuição da Carteira")
            if dados_carteira['valor_atual_total'] > 0:
                posicao_atual_df['% Atual'] = (posicao_atual_df['Valor Atual'] / dados_carteira['valor_atual_total']) * 100
                
                # Coleta todos os tickers únicos para garantir cores consistentes
                all_tickers_in_charts = set(posicao_atual_df['ticker'].tolist())
                col1, col2 = st.columns(2)
                with col1:
                    alocacao_ideal_df = carregar_alocacao_ideal_usuario(email_usuario)
                    tickers_atuais = posicao_atual_df['ticker'].tolist()
                    alocacao_ideal_filtrada = alocacao_ideal_df[alocacao_ideal_df['ticker'].isin(tickers_atuais)]
                    all_tickers_in_charts.update(alocacao_ideal_filtrada['ticker'].tolist())

                    # Get sorted list of tickers for category_orders
                    sorted_tickers_for_charts = sorted(list(all_tickers_in_charts))
                    # Cria um mapa de cores consistente para todos os tickers relevantes
                    color_palette = px.colors.qualitative.Dark24 # Paleta com 24 cores distintas
                    ticker_color_map = {ticker: color_palette[i % len(color_palette)] for i, ticker in enumerate(sorted_tickers_for_charts)}

                    fig_pie_atual = px.pie(
                        posicao_atual_df.sort_values(by="ticker"),
                        values='% Atual',
                        names='ticker',
                        category_orders={"ticker": sorted_tickers_for_charts},
                        title='Distribuição Atual',
                        hole=.3,
                        color='ticker',
                        color_discrete_map=ticker_color_map
                    )
                    st.plotly_chart(fig_pie_atual, use_container_width=True, key=f"carteira_pie_chart_atual_{email_usuario}_carteira")

                alocacao_ideal_df = carregar_alocacao_ideal_usuario(email_usuario)
                with col2:
                    # Filtra a alocação ideal para conter apenas tickers que o usuário ainda possui
                    tickers_atuais = posicao_atual_df['ticker'].tolist()
                    alocacao_ideal_filtrada = alocacao_ideal_df[alocacao_ideal_df['ticker'].isin(tickers_atuais)]
                    if not alocacao_ideal_filtrada.empty and alocacao_ideal_filtrada['percentual_alvo'].sum() > 0:
                        fig_pie_ideal = px.pie(alocacao_ideal_filtrada.sort_values(by="ticker"),
                            values='percentual_alvo',
                            names='ticker',
                            title='Distribuição Ideal',
                            category_orders={"ticker": sorted_tickers_for_charts},
                            hole=.3,
                            color='ticker',
                            color_discrete_map=ticker_color_map
                        )
                        st.plotly_chart(fig_pie_ideal, use_container_width=True, key=f"carteira_pie_chart_ideal_{email_usuario}_carteira")
                    else:
                        st.markdown("<div style='text-align: center; padding-top: 80px;'>Defina sua alocação ideal para visualizar o gráfico.</div>", unsafe_allow_html=True)

            with st.expander("Definir/Ajustar Alocação Ideal da Carteira", expanded=True):
                alocacao_ideal_df = carregar_alocacao_ideal_usuario(email_usuario)
                with st.form("form_alocacao_ideal_carteira"):
                    if '% Atual' not in posicao_atual_df.columns:
                        if dados_carteira['valor_atual_total'] > 0:
                            posicao_atual_df['% Atual'] = (posicao_atual_df['Valor Atual'] / dados_carteira['valor_atual_total']) * 100
                        else:
                            posicao_atual_df['% Atual'] = 0

                    alocacao_df_merged = pd.merge(posicao_atual_df[['ticker', '% Atual', 'Valor Atual']], alocacao_ideal_df, on='ticker', how='left').fillna(0)

                    target_inputs = {}
                    st.markdown("Defina o percentual alvo para cada ativo na sua carteira. A soma deve ser 100%.")

                    ch1, ch2, ch3, ch4 = st.columns([2, 2, 2, 3])
                    ch1.markdown("**Ativo**"); ch2.markdown("**% Atual**"); ch3.markdown("**% Alvo**"); ch4.markdown("**Ajuste Necessário (R$)**")

                    for index, row in alocacao_df_merged.iterrows():
                        c1, c2, c3, c4 = st.columns([2, 2, 2, 3])
                        c1.write(f"**{row['ticker']}**"); c2.write(f"{row['% Atual']:.2f}%")
                        target_percent = c3.number_input("Alvo (%)", min_value=0.0, max_value=100.0, value=float(row['percentual_alvo']), step=1.0, key=f"target_carteira_{row['ticker']}", label_visibility="collapsed")
                        target_inputs[row['ticker']] = target_percent

                        valor_total_carteira = dados_carteira['valor_atual_total']
                        if valor_total_carteira > 0 and pd.notna(row.get('Valor Atual')):
                            valor_alvo_ativo = valor_total_carteira * (target_percent / 100.0)
                            ajuste_necessario = valor_alvo_ativo - row['Valor Atual']
                            
                            cor = "green" if ajuste_necessario > 0 else "red" if ajuste_necessario < 0 else "gray"
                            sinal = "Comprar" if ajuste_necessario > 0 else "Vender" if ajuste_necessario < 0 else "Manter"
                            
                            if sinal == "Manter":
                                texto_ajuste = f"<div style='color:{cor}; text-align: left;'>R$ 0,00</div>"
                            else:
                                texto_ajuste = f"<div style='color:{cor}; text-align: left;'>{sinal} R$ {abs(ajuste_necessario):,.2f}</div>"
                            c4.markdown(texto_ajuste, unsafe_allow_html=True)
                        else:
                            c4.write("N/A")

                    total_alvo = sum(target_inputs.values())
                    if abs(total_alvo - 100.0) > 0.1:
                        st.warning(f"A soma dos percentuais alvo é **{total_alvo:.2f}%**. O ideal é que a soma seja 100%.")
                    else:
                        st.success(f"Soma dos percentuais alvo: {total_alvo:.2f}%")

                    if st.form_submit_button("Salvar Alocação Ideal", use_container_width=True):
                        df_para_salvar = pd.DataFrame(list(target_inputs.items()), columns=['ticker', 'percentual_alvo'])
                        salvar_alocacao_ideal_usuario(email_usuario, df_para_salvar)

            alertas_usuario = carregar_alertas_usuario(email_usuario)
            if not alertas_usuario.empty:
                posicao_atual_df = pd.merge(posicao_atual_df, alertas_usuario[['ticker', 'preco_alvo']], on='ticker', how='left')
            else:
                posicao_atual_df['preco_alvo'] = None
            posicao_atual_df['preco_alvo'].fillna(0.0, inplace=True)
            
            st.subheader("Posição Atual e Alertas de Preço")
            header_cols = st.columns([2, 1, 2, 2, 2, 1.5, 1.5, 3])
            headers = ['Ticker', 'Qtd.', 'Preço Médio', 'Preço Atual', 'Valor Atual', 'Lucro Não Realizado', 'Prejuízo Não Realizado', 'Alerta de Preço (R$)']
            for col, header in zip(header_cols, headers): col.markdown(f"**{header}**")
            st.markdown("---")

            for index, row in posicao_atual_df.iterrows():
                row_cols = st.columns([2, 1, 2, 2, 2, 1.5, 1.5, 3])
                row_cols[0].write(f"**{row['ticker']}**")
                row_cols[1].write(f"{row['quantidade_atual']:.2f}")
                row_cols[2].write(f"R$ {row['preco_medio_ponderado']:.2f}")

                # Verifica se os dados de mercado estão disponíveis antes de formatar
                preco_atual_str = f"R$ {row['Preço Atual']:.2f}" if pd.notna(row['Preço Atual']) else "N/A"
                row_cols[3].write(preco_atual_str)

                valor_atual_str = f"R$ {row['Valor Atual']:.2f}" if pd.notna(row['Valor Atual']) else "N/A"
                row_cols[4].write(valor_atual_str)

                # Separar lucro e prejuízo não realizados
                if pd.notna(row['Lucro/Prejuízo Não Realizado']):
                    lp_valor = row['Lucro/Prejuízo Não Realizado']
                    if lp_valor > 0:
                        row_cols[5].markdown(f"<span style='color:green'>R$ {lp_valor:.2f}</span>", unsafe_allow_html=True)
                        row_cols[6].write("-")
                    else:
                        row_cols[5].write("-")
                        row_cols[6].markdown(f"<span style='color:red'>R$ {abs(lp_valor):.2f}</span>", unsafe_allow_html=True)
                else:
                    row_cols[5].write("N/A")
                    row_cols[6].write("N/A")
                
                with row_cols[7]:
                    valor_input = st.number_input("Preço Alvo", min_value=0.0, value=float(row['preco_alvo']), format="%.2f", label_visibility="collapsed", key=f"alert_input_{row['ticker']}")
                    if st.button("Salvar", key=f"save_alert_{row['ticker']}", use_container_width=True):
                        salvar_alerta(email_usuario, row['ticker'], valor_input)

    with st.expander("Gerenciar Transações Individuais", expanded=True):
        carteira_individual_df = carregar_carteira_usuario(email_usuario)
        if carteira_individual_df.empty:
            st.write("Nenhuma transação para gerenciar.")
        else:
            if 'tipo' not in carteira_individual_df.columns:
                carteira_individual_df['tipo'] = 'Compra'
            carteira_individual_df['tipo'].fillna('Compra', inplace=True)

            c = st.columns([1, 3, 2, 2, 2, 1, 1])
            c[0].write("**Tipo**"); c[1].write("**Ticker**"); c[2].write("**Qtd.**"); c[3].write("**Preço**"); c[4].write("**Data**")
            for index, row in carteira_individual_df.sort_values(by='data_compra', ascending=False).iterrows():
                c = st.columns([1, 3, 2, 2, 2, 1, 1])
                tipo_transacao = row.get('tipo', 'Compra')
                cor_tipo = "red" if tipo_transacao == "Venda" else "green"
                c[0].markdown(f"<span style='color:{cor_tipo};'>{tipo_transacao}</span>", unsafe_allow_html=True)
                c[1].write(row['ticker'])
                c[2].write(f"{row['quantidade']:.4f}".rstrip('0').rstrip('.'))
                c[3].write(f"R$ {row['preco_compra']:.2f}")
                c[4].write(f"{pd.to_datetime(row['data_compra']).strftime('%d/%m/%Y')}")
                if c[5].button("✏️", key=f"edit_carteira_{index}", help="Editar esta transação"):
                    st.session_state.editing_transaction_id = index
                    st.rerun()
                if c[6].button("🗑️", key=f"delete_carteira_{index}", help="Remover esta transação"):
                    remover_ativo_carteira(email_usuario, index)
                    st.toast(f"Transação de {row['ticker']} removida!", icon="🗑️")
                    st.rerun()
    st.markdown("""
    <p style='text-align: center; font-size: 0.8em;'>
        <a href='https://github.com/leandrovcorrea' target='_blank' style='color: blue; text-decoration: none; display: inline-flex; align-items: center;'>
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" viewBox="0 0 16 16" style="margin-right: 5px;">
                <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38C13.71 14.53 16 11.54 16 8c0-4.42-3.58-8-8-8"/>
            </svg>
            Criado por Leandrovcorrea
        </a>
    </p>
    """, unsafe_allow_html=True)

    # --- Funções da Watchlist ---
WATCHLIST_FILE = "watchlist.csv"

def carregar_watchlist_usuario(email_usuario):
    """Carrega a watchlist de um usuário específico."""
    if not os.path.exists(WATCHLIST_FILE):
        return []
    df = pd.read_csv(WATCHLIST_FILE)
    user_watchlist = df[df['email_usuario'] == email_usuario]
    return user_watchlist['ticker'].tolist()

def adicionar_ticker_watchlist(email_usuario, ticker):
    """Adiciona um ticker à watchlist de um usuário."""
    if not os.path.exists(WATCHLIST_FILE):
        df = pd.DataFrame(columns=['email_usuario', 'ticker'])
        df.to_csv(WATCHLIST_FILE, index=False)
    else:
        df = pd.read_csv(WATCHLIST_FILE)

    if not ((df['email_usuario'] == email_usuario) & (df['ticker'] == ticker)).any():
        novo_item = pd.DataFrame([{'email_usuario': email_usuario, 'ticker': ticker}])
        df = pd.concat([df, novo_item], ignore_index=True)
        df.to_csv(WATCHLIST_FILE, index=False)
        st.toast(f"'{ticker.replace('.SA', '')}' adicionado à watchlist!", icon="✅")
    else:
        st.warning(f"'{ticker.replace('.SA', '')}' já está na sua watchlist.")
    st.rerun()

def remover_ticker_watchlist(email_usuario, ticker):
    """Remove um ticker da watchlist de um usuário."""
    if not os.path.exists(WATCHLIST_FILE):
        return
    df = pd.read_csv(WATCHLIST_FILE)
    
    filtro = (df['email_usuario'] == email_usuario) & (df['ticker'] == ticker)
    if filtro.any():
        df = df[~filtro]
        df.to_csv(WATCHLIST_FILE, index=False)
        st.toast(f"'{ticker.replace('.SA', '')}' removido da watchlist.", icon="🗑️")
    st.rerun()

def pagina_dashboard():
    """Renderiza a página do dashboard com um resumo da carteira."""
    st.header("🏠 Dashboard")
    email_usuario = st.session_state.usuario_logado['email']

    st.info("Bem-vindo ao Dashboard! Aqui você verá um resumo da sua carteira e indicadores principais.")
    st.markdown("---")

    st.subheader("Resumo da Carteira")
    with st.spinner("Carregando dados da carteira..."):
        dados_carteira = calcular_dados_carteira(email_usuario)
        verificar_e_enviar_alertas(email_usuario, dados_carteira) # Verifica alertas ao carregar o dashboard

    if dados_carteira.get("erro") == "Carteira vazia":
        st.info("Sua carteira está vazia. Adicione uma transação na aba 'Minha Carteira' para ver o resumo aqui.")
    elif dados_carteira.get("erro"):
        st.error(f"Ocorreu um erro ao carregar o resumo da sua carteira: {dados_carteira['erro']}")
    else:
        c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
        with c1:
            st.markdown("<div style='text-align:center; margin-bottom:10px;'>Custo Total</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align:center; font-size:1.3rem; font-weight:600; height:40px; display:flex; align-items:center; justify-content:center;'>R$ {dados_carteira['total_investido']:,.2f}</div>", unsafe_allow_html=True)
        with c2:
            st.markdown("<div style='text-align:center; margin-bottom:10px;'>Valor Atual</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align:center; font-size:1.3rem; font-weight:600; height:40px; display:flex; align-items:center; justify-content:center;'>R$ {dados_carteira['valor_atual_total']:,.2f}</div>", unsafe_allow_html=True)
        with c3:
            st.markdown("<div style='text-align:center; margin-bottom:10px;'>Lucro Não Realizado</div>", unsafe_allow_html=True)
            posicao_atual_df = dados_carteira.get('posicao_atual_df')
            if posicao_atual_df is not None and not posicao_atual_df.empty and 'Lucro/Prejuízo Não Realizado' in posicao_atual_df.columns:
                lucros_positivos = posicao_atual_df[posicao_atual_df['Lucro/Prejuízo Não Realizado'] > 0]['Lucro/Prejuízo Não Realizado'].sum()
                st.markdown(f"<div style='text-align:center; color:green; font-size:1.6rem; font-weight:600; height:40px; display:flex; align-items:center; justify-content:center;'>R$ {lucros_positivos:,.2f}</div>", unsafe_allow_html=True)
            else:
                st.markdown(f"<div style='text-align:center; font-size:1.6rem; font-weight:600; height:40px; display:flex; align-items:center; justify-content:center;'>R$ 0,00</div>", unsafe_allow_html=True)
        with c4:
            st.markdown("<div style='text-align:center; margin-bottom:10px;'>Prejuízo Não Realizado</div>", unsafe_allow_html=True)
            posicao_atual_df = dados_carteira.get('posicao_atual_df')
            if posicao_atual_df is not None and not posicao_atual_df.empty and 'Lucro/Prejuízo Não Realizado' in posicao_atual_df.columns:
                prejuizos_negativos = posicao_atual_df[posicao_atual_df['Lucro/Prejuízo Não Realizado'] < 0]['Lucro/Prejuízo Não Realizado'].sum()
                st.markdown(f"<div style='text-align:center; color:red; font-size:1.3rem; font-weight:600; height:40px; display:flex; align-items:center; justify-content:center;'>R$ {abs(prejuizos_negativos):,.2f}</div>", unsafe_allow_html=True)
            else:
                st.markdown(f"<div style='text-align:center; font-size:1.6rem; font-weight:600; height:40px; display:flex; align-items:center; justify-content:center;'>R$ 0,00</div>", unsafe_allow_html=True)
        with c5:
            st.markdown("<div style='text-align:center; margin-bottom:10px;'>Lucro Realizado</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align:center; color:green; font-size:1.3rem; font-weight:600; height:40px; display:flex; align-items:center; justify-content:center;'>R$ {dados_carteira['lucros_realizados_total']:,.2f}</div>", unsafe_allow_html=True)
        with c6:
            st.markdown("<div style='text-align:center; margin-bottom:10px;'>Prejuízo Realizado</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align:center; color:red; font-size:1.3rem; font-weight:600; height:40px; display:flex; align-items:center; justify-content:center;'>R$ {abs(dados_carteira['prejuizos_realizados_total']):,.2f}</div>", unsafe_allow_html=True)
        with c7:
            st.markdown("<div style='text-align:center; margin-bottom:10px;'>Dividendos Recebidos</div>", unsafe_allow_html=True)
            st.markdown(f"<div style='text-align:center; color:blue; font-size:1.3rem; font-weight:600; height:40px; display:flex; align-items:center; justify-content:center;'>R$ {dados_carteira['total_dividendos_recebidos']:,.2f}</div>", unsafe_allow_html=True)

        st.markdown("---")
        st.subheader("Evolução do Patrimônio")

        # Mapeamento de índices para exibição com ícones/emojis
        opcoes_indices_display = {
            "IBOV": "🇧🇷 IBOV",
            "S&P 500 (SPX)": "🇺🇸 S&P 500",
            "SMLL": "📈 SMLL",
            "IVVB11": "🌍 IVVB11"
        }

        # Inicializa os índices selecionados no estado da sessão, se ainda não existirem
        if 'dashboard_selected_indices' not in st.session_state:
            st.session_state.dashboard_selected_indices = [] # Nenhum índice selecionado por padrão

        st.write("Comparar rentabilidade com:")
        cols = st.columns(len(opcoes_indices_display)) # Cria colunas para cada opção

        for i, (key, display_name) in enumerate(opcoes_indices_display.items()):
            with cols[i]:
                is_selected = key in st.session_state.dashboard_selected_indices
                button_type = "primary" if is_selected else "secondary"
                if st.button(display_name, key=f"index_btn_{key}", type=button_type, use_container_width=True):
                    if is_selected: st.session_state.dashboard_selected_indices.remove(key)
                    else: st.session_state.dashboard_selected_indices.append(key)
                    st.rerun() # Recarrega a página para atualizar os botões e o gráfico

        indices_selecionados = st.session_state.dashboard_selected_indices

        with st.spinner("Gerando gráfico de evolução..."):
            fig_evolucao = gerar_grafico_evolucao_patrimonio(email_usuario, indices_selecionados)
            if fig_evolucao:
                st.plotly_chart(fig_evolucao, use_container_width=True)
            else:
                st.info("Não foi possível gerar o gráfico de evolução do patrimônio. Verifique se há transações válidas e dados de preço disponíveis.")

        st.markdown("---")
        st.subheader("Distribuição da Carteira")
        posicao_atual_df = dados_carteira['posicao_atual_df']
        if not posicao_atual_df.empty and dados_carteira['valor_atual_total'] > 0:
            # Coleta todos os tickers únicos para garantir cores consistentes
            all_tickers_in_charts = set(posicao_atual_df['ticker'].tolist())
            col1, col2 = st.columns(2)
            with col1:
                # Get sorted list of tickers for category_orders
                sorted_tickers_for_charts = sorted(list(all_tickers_in_charts))
                posicao_atual_df['% Atual'] = (posicao_atual_df['Valor Atual'] / dados_carteira['valor_atual_total']) * 100
                alocacao_ideal_df = carregar_alocacao_ideal_usuario(email_usuario)
                tickers_atuais = posicao_atual_df['ticker'].tolist()
                alocacao_ideal_filtrada = alocacao_ideal_df[alocacao_ideal_df['ticker'].isin(tickers_atuais)]
                all_tickers_in_charts.update(alocacao_ideal_filtrada['ticker'].tolist())

                # Cria um mapa de cores consistente para todos os tickers relevantes
                color_palette = px.colors.qualitative.Dark24 # Paleta com 24 cores distintas
                ticker_color_map = {ticker: color_palette[i % len(color_palette)] for i, ticker in enumerate(sorted_tickers_for_charts)}

                fig_pie_atual = px.pie(
                    posicao_atual_df.sort_values(by="ticker"),
                    values='% Atual',
                    names='ticker',
                    category_orders={"ticker": sorted_tickers_for_charts},
                    title='Distribuição Atual',
                    hole=.3,
                    color='ticker',
                    color_discrete_map=ticker_color_map
                )
                st.plotly_chart(fig_pie_atual, use_container_width=True, key=f"dashboard_pie_chart_atual_{email_usuario}")
            
            with col2:
                # Filtra a alocação ideal para conter apenas tickers que o usuário ainda possui
                tickers_atuais = posicao_atual_df['ticker'].tolist()
                alocacao_ideal_filtrada = alocacao_ideal_df[alocacao_ideal_df['ticker'].isin(tickers_atuais)]
                if not alocacao_ideal_filtrada.empty and alocacao_ideal_filtrada['percentual_alvo'].sum() > 0:
                    fig_pie_ideal = px.pie(alocacao_ideal_filtrada.sort_values(by="ticker"),
                        values='percentual_alvo',
                        names='ticker',
                        title='Distribuição Ideal',
                        category_orders={"ticker": sorted_tickers_for_charts},
                        hole=.3,
                        color='ticker',
                        color_discrete_map=ticker_color_map
                    )
                    st.plotly_chart(fig_pie_ideal, use_container_width=True, key=f"dashboard_pie_chart_ideal_{email_usuario}")
                else:
                    st.markdown("<div style='text-align: center; padding-top: 80px;'>Defina sua alocação ideal na aba 'Minha Carteira' para visualizar o gráfico.</div>", unsafe_allow_html=True)
        else:
            st.info("Não há ativos em sua carteira para exibir a distribuição.")
    st.markdown("""
    <p style='text-align: center; font-size: 0.8em;'>
        <a href='https://github.com/leandrovcorrea' target='_blank' style='color: blue; text-decoration: none; display: inline-flex; align-items: center;'>
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" viewBox="0 0 16 16" style="margin-right: 5px;">
                <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38C13.71 14.53 16 11.54 16 8c0-4.42-3.58-8-8-8"/>
            </svg>
            Criado por Leandrovcorrea
        </a>
    </p>
    """, unsafe_allow_html=True)

def pagina_analise():
    """Renderiza a página de análise de ativos."""
    st.header("📊 Análise de Ativos")
    
    col1, col2 = st.columns([3, 1])
    with col1:
        st.text_input(
            "Digite o ticker e pressione Enter",
            key="ticker_input_key",
            on_change=iniciar_analise,
            placeholder="Ex: AAPL, MSFT, GOOG, MXRF11.SA",
            label_visibility="collapsed"
        )
    with col2:
        st.button("Analisar Ação", on_click=iniciar_analise, use_container_width=True, type="primary")
    
    st.subheader("Parâmetros dos Modelos de Valuation")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.number_input("Taxa de crescimento (Ações) (%)", min_value=-10.0, max_value=100.0, value=5.0, step=0.5, key="taxa_crescimento_input", help="Taxa de crescimento anual estimada (%) para o modelo 'Preço Justo de Graham'.")
    with c2:
        st.number_input("Rendimento Títulos (Y) (%)", min_value=0.1, max_value=20.0, value=4.5, step=0.1, key="bond_yield_input", help="Rendimento dos títulos do Tesouro Americano de 10 anos (usado no modelo de Graham).")
    with c3:
        st.number_input("DY Desejado para FIIs (%)", min_value=1.0, max_value=25.0, value=8.0, step=0.5, key="dy_desejado_input", help="Dividend Yield anual desejado para calcular o preço-teto de FIIs.")

    st.markdown("---")
    if st.session_state.get('input_error'):
        st.error(f"❌ Erro de Validação: {st.session_state.input_error}")

    if st.session_state.get('ticker_analisado'):
        ticker_para_analise = st.session_state.ticker_analisado
        with st.spinner(f"Buscando dados para {ticker_para_analise}..."):
            dados_acao = obter_dados_acao(ticker_para_analise)

        if "erro" in dados_acao:
            st.error(f"❌ Erro ao obter dados: {dados_acao['erro']}")
        else:
            if st.session_state.get('ticker_foi_ajustado', False):
                company_name = dados_acao.get('longName') or dados_acao.get('symbol')
                st.success(f"Analisando **{company_name}**")
                st.session_state.ticker_foi_ajustado = False

            # --- Detecção de tipo de ativo ---
            quote_type = dados_acao.get('quoteType')
            is_fii = (quote_type == 'ETF' and ticker_para_analise.endswith('11.SA'))

            # --- Análise Técnica (TradingView) ---
            with st.spinner(f"Buscando análise técnica para {ticker_para_analise.replace('.SA', '')}..."):
                analise_tecnica = obter_analise_tecnica_tradingview(ticker_para_analise)

            if "erro" not in analise_tecnica:
                st.subheader("Análise Técnica (TradingView - Diário)")
                recomendacao = analise_tecnica.get('recomendacao', 'N/A')

                # Mapeamento de tradução para a recomendação
                traducao_recomendacao = {
                    "STRONG_BUY": "COMPRA FORTE",
                    "BUY": "COMPRA",
                    "NEUTRAL": "NEUTRO",
                    "SELL": "VENDA",
                    "STRONG_SELL": "VENDA FORTE",
                    "N/A": "N/A"
                }
                recomendacao_pt = traducao_recomendacao.get(recomendacao, recomendacao)
                
                contadores = analise_tecnica.get('contadores', {})
                cor_recomendacao = "green" if "BUY" in recomendacao else "red" if "SELL" in recomendacao else "orange"
                st.markdown(f"#### Recomendação Geral: <span style='color:{cor_recomendacao};'>{recomendacao_pt}</span>", unsafe_allow_html=True)
                
                c1, c2, c3 = st.columns(3)
                c1.metric("Indicadores de Compra", contadores.get('BUY', 0))
                c2.metric("Indicadores Neutros", contadores.get('NEUTRAL', 0))
                c3.metric("Indicadores de Venda", contadores.get('SELL', 0))
            else:
                st.warning(f"Não foi possível obter a análise técnica: {analise_tecnica['erro']}")
            st.markdown("---")

            resultados = []
            if is_fii:
                st.info("Este ativo foi identificado como um Fundo Imobiliário (FII). Modelos de valuation para ações (Graham) não serão aplicados.")
                resultados.append(calcular_preco_teto_bazin(dados_acao))
                resultados.append(calcular_preco_teto_fii(dados_acao, st.session_state.dy_desejado_input))
            else: # É uma ação (ou outro tipo não FII)
                lpa = dados_acao.get('trailingEps')
                vpa = dados_acao.get('bookValue')

                if lpa is not None and lpa > 0:
                    resultados.append(calcular_preco_justo_graham(dados_acao, st.session_state.taxa_crescimento_input, st.session_state.bond_yield_input))
                else:
                    lpa_formatado = f"{lpa:.2f}" if lpa is not None else "N/A"
                    resultados.append({"modelo": "Preço Justo de Graham", "erro": f"LPA nulo ou negativo (LPA atual: {lpa_formatado})."})

                if lpa is not None and lpa > 0 and vpa is not None and vpa > 0:
                    resultados.append(calcular_numero_graham(dados_acao))
                else:
                    erros_graham_num = []
                    if lpa is None or lpa <= 0: erros_graham_num.append("LPA nulo/negativo")
                    if vpa is None or vpa <= 0: erros_graham_num.append("VPA nulo/negativo")
                    resultados.append({"modelo": "Número de Graham", "erro": f"Pré-requisitos não atendidos: {', '.join(erros_graham_num)}."})

                resultados.append(calcular_preco_teto_bazin(dados_acao))

            exibir_resultados_comparativos(resultados)
            st.write("---")
            exibir_indicadores_chave(dados_acao)
            st.write("---")

            if 'historico' not in st.session_state: st.session_state.historico = []
            for res in resultados:
                if "erro" not in res: st.session_state.historico.insert(0, res)

            exibir_grafico_precos_interativo(dados_acao['historico_precos'], dados_acao['symbol'])
            exibir_grafico_dividendos(dados_acao)
    else:
        st.info("Digite o ticker de uma ação acima para iniciar a análise.")
    st.markdown("""
    <p style='text-align: center; font-size: 0.8em;'>
        <a href='https://github.com/leandrovcorrea' target='_blank' style='color: blue; text-decoration: none; display: inline-flex; align-items: center;'>
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" viewBox="0 0 16 16" style="margin-right: 5px;">
                <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38C13.71 14.53 16 11.54 16 8c0-4.42-3.58-8-8-8"/>
            </svg>
            Criado por Leandrovcorrea
        </a>
    </p>
    """, unsafe_allow_html=True)

# --- Funções de Alocação Ideal ---
ALOCACAO_FILE = "alocacao_ideal.csv"

def carregar_alocacao_ideal_usuario(email_usuario):
    """Carrega a alocação ideal de um usuário de forma robusta."""
    if not os.path.exists(ALOCACAO_FILE):
        return pd.DataFrame(columns=['ticker', 'percentual_alvo'])
    try:
        df = pd.read_csv(ALOCACAO_FILE)
        # Se o arquivo estiver vazio ou não tiver as colunas esperadas, retorna um DF vazio
        if df.empty or not all(col in df.columns for col in ['email_usuario', 'ticker', 'percentual_alvo']):
            return pd.DataFrame(columns=['ticker', 'percentual_alvo'])
        user_alloc = df[df['email_usuario'] == email_usuario].copy()
        if user_alloc.empty:
            return pd.DataFrame(columns=['ticker', 'percentual_alvo'])
        return user_alloc[['ticker', 'percentual_alvo']]
    except pd.errors.EmptyDataError:
        # Ocorre se o arquivo CSV existir mas estiver completamente vazio
        return pd.DataFrame(columns=['ticker', 'percentual_alvo'])

def salvar_alocacao_ideal_usuario(email_usuario, df_alocacao):
    """Salva a alocação ideal completa de um usuário de forma robusta."""
    expected_cols = ['email_usuario', 'ticker', 'percentual_alvo']
    df_geral = pd.DataFrame(columns=expected_cols)

    if os.path.exists(ALOCACAO_FILE):
        try:
            df_lido = pd.read_csv(ALOCACAO_FILE)
            if not df_lido.empty:
                df_geral = df_lido
        except pd.errors.EmptyDataError:
            pass

    if 'email_usuario' in df_geral.columns:
        df_geral = df_geral[df_geral['email_usuario'] != email_usuario]

    df_alocacao_com_email = df_alocacao.copy()
    df_alocacao_com_email['email_usuario'] = email_usuario
    df_alocacao_com_email = df_alocacao_com_email[df_alocacao_com_email['percentual_alvo'] > 0]
    df_final = pd.concat([df_geral, df_alocacao_com_email], ignore_index=True)
    df_final.to_csv(ALOCACAO_FILE, index=False, columns=expected_cols)
    st.toast("Alocação ideal salva com sucesso!", icon="🎯")
    st.rerun()

def pagina_watchlist():
    """Renderiza a página da watchlist do usuário."""
    st.header("👁️ Minha Watchlist")
    email_usuario = st.session_state.usuario_logado['email']

    with st.form("form_add_watchlist", clear_on_submit=True):
        ticker_input = st.text_input("Adicionar Ticker à Watchlist", placeholder="Ex: MGLU3, TSLA").upper()
        if st.form_submit_button("Adicionar", use_container_width=True):
            if ticker_input:
                ticker_final = ticker_input
                if '.' not in ticker_final and any(char.isdigit() for char in ticker_final):
                    ticker_final = f"{ticker_final}.SA"
                
                with st.spinner(f"Validando {ticker_final}..."):
                    dados_validacao = obter_dados_acao(ticker_final)
                
                if "erro" in dados_validacao:
                    st.error(f"Ticker '{ticker_final}' inválido: {dados_validacao['erro']}")
                else:
                    adicionar_ticker_watchlist(email_usuario, ticker_final)
    
    st.markdown("---")
    watchlist = carregar_watchlist_usuario(email_usuario)

    if not watchlist:
        st.info("Sua watchlist está vazia. Adicione um ticker acima.")
        return

    try:
        with st.spinner("Atualizando dados da watchlist..."):
            if not watchlist:
                dados_precos_raw = pd.DataFrame()
            else:
                tickers_string = " ".join(watchlist)
                dados_precos_raw = yf.download(tickers=tickers_string, period='2d', progress=False, group_by='ticker')
    except Exception as e:
        st.error(f"Ocorreu um erro ao buscar os dados da watchlist: {e}")
        dados_precos_raw = pd.DataFrame()

    st.subheader("Ativos Acompanhados")
    
    for ticker in sorted(watchlist):
        info_ticker = obter_info_empresa(ticker)
        nome_empresa = info_ticker.get('longName', ticker.replace('.SA', ''))

        preco_atual, variacao = None, None
        try:
            if ticker in dados_precos_raw.columns:
                dados_ticker = dados_precos_raw[ticker]
                if not dados_ticker.empty and len(dados_ticker['Close'].dropna()) >= 2:
                    preco_atual = dados_ticker['Close'].dropna().iloc[-1]
                    preco_anterior = dados_ticker['Close'].dropna().iloc[-2]
                    variacao = ((preco_atual - preco_anterior) / preco_anterior) * 100
        except (KeyError, IndexError):
            pass

        cols = st.columns([4, 2, 1, 1])
        cols[0].markdown(f"**{nome_empresa}**<br><small>{ticker.replace('.SA', '')}</small>", unsafe_allow_html=True)
        
        if preco_atual is not None and variacao is not None:
            cols[1].metric(label="Preço Atual", value=f"R$ {preco_atual:.2f}", delta=f"{variacao:+.2f}%", label_visibility="collapsed")
        else:
            cols[1].metric(label="Preço Atual", value="N/A", delta="", label_visibility="collapsed")

        if cols[2].button("Analisar", key=f"analise_wl_{ticker}", use_container_width=True):
            st.session_state.ticker_input_key = ticker
            iniciar_analise()
            st.session_state.sinalizar_analise_ativa = True  # Sinaliza que o usuário quer ir para a aba de análise
            st.toast(f"Análise de {ticker.replace('.SA', '')} pronta! Clique na aba 'Análise de Ativos' para ver.", icon="📊")

        if cols[3].button("Remover", key=f"remove_wl_{ticker}", use_container_width=True):
            remover_ticker_watchlist(email_usuario, ticker)
            
        st.markdown("<hr style='margin-top:0.5rem; margin-bottom:0.5rem;'>", unsafe_allow_html=True)

    st.markdown("---")
    st.header("Notícias Recentes da Watchlist")
    
    if watchlist:
        with st.spinner("Buscando notícias da watchlist..."):
            noticias_watchlist = obter_noticias_ativos(watchlist)
        
        if not noticias_watchlist:
            st.info("Nenhuma notícia recente encontrada para os ativos da sua watchlist.")
        else:
            for ticker, lista_noticias in noticias_watchlist.items():
                st.subheader(f"Notícias para {ticker.replace('.SA', '')}")
                
                # Filtra notícias para garantir que tenham título e link, evitando entradas vazias.
                noticias_validas = [n for n in lista_noticias if n.get('title') and n.get('link')]

                if not noticias_validas:
                    st.write("Nenhuma notícia com título e link foi encontrada para este ativo.")
                    continue

                for noticia in noticias_validas:
                    provider_time = noticia.get('providerPublishTime')
                    data_publicacao = pd.to_datetime(provider_time, unit='s').strftime('%d/%m/%Y %H:%M') if provider_time else "Data Indisponível"
                    publisher = noticia.get('publisher', 'Fonte Indisponível')
                    st.markdown(f"**<a href='{noticia['link']}' target='_blank' style='text-decoration: none; color: inherit;'>{noticia['title']}</a>**", unsafe_allow_html=True)
                    st.caption(f"Fonte: {publisher} | Publicado em: {data_publicacao}")
    else:
        st.info("Adicione ativos à sua watchlist para ver notícias relacionadas.")
    st.markdown("""
    <p style='text-align: center; font-size: 0.8em;'>
        <a href='https://github.com/leandrovcorrea' target='_blank' style='color: blue; text-decoration: none; display: inline-flex; align-items: center;'>
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" viewBox="0 0 16 16" style="margin-right: 5px;">
                <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38C13.71 14.53 16 11.54 16 8c0-4.42-3.58-8-8-8"/>
            </svg>
            Criado por Leandrovcorrea
        </a>
    </p>
    """, unsafe_allow_html=True)


def exibir_tabela_acoes():
    ativos = ['ITUB4', 'BBDC4', 'SBSP3', 'B3SA3', 'ITSA4', 'WEGE3', 'BBAS3', 'ABEV3', 'BPAC11', 'ITUB4', 'PRIO3', 'BBDC4', 'BBSE3', 'TOTS3', 'SBSP3', 'BBDC3', 'B3SA3', 'CMIG4', 'ITSA4', 'WEGE3', 'TIMS3', 'BBAS3', 'ABEV3', 'BPAC11', 'ITUB3', 'EGIE3', 'ISAE4', 'PRIO3', 'CMIN3', 'CPFE3', 'SAPR11', 'CXSE3', 'CYRE3', 'BBSE3', 'TOTS3', 'BBDC3', 'CMIG4', 'POMO4', 'CSMG3', 'DIRR3', 'TIMS3', 'ITUB3', 'CURY3', 'EGIE3', 'ODPV3', 'UNIP6', 'ISAE4', 'FRAS3', 'CMIN3', 'CPFE3', 'CXSE3', 'SAPR11', 'INTB3', 'CYRE3', 'ABCB4', 'LEVE3', 'SAPR4']
    pesos = [15.87, 6.89, 6.38, 5.79, 5.17, 5.09, 4.97, 4.75, 4.46, 4.36, 2.63, 1.89, 1.84, 1.80, 1.76, 1.71, 1.60, 1.55, 1.42, 1.40, 1.38, 1.37, 1.31, 1.23, 1.18, 0.90, 0.75, 0.72, 0.65, 0.62, 0.61, 0.61, 0.53, 0.51, 0.50, 0.47, 0.43, 0.42, 0.42, 0.38, 0.38, 0.33, 0.32, 0.25, 0.24, 0.24, 0.21, 0.20, 0.18, 0.17, 0.17, 0.17, 0.15, 0.15, 0.13, 0.13, 0.12, 0.12, 0.12, 0.11, 0.09, 0.07, 0.07, 0.06, 0.04, 0.04, 0.04, 0.03]
    categorias = ['Ações'] * len(ativos)
    data = {
        'CATEGORIA': categorias,
        'ATIVO': ativos,
        'PESO (%)': pesos
    }
    df = pd.DataFrame(data)
    st.dataframe(df, use_container_width=True, hide_index=True)

# --- Funções de Backtesting ---
def run_ma_crossover_backtest(ticker, start_date, end_date, short_ma_period, long_ma_period, initial_capital):
    """
    Executa um backtest de uma estratégia de Média Móvel Crossover.
    Compra quando a MA curta cruza acima da MA longa.
    Vende quando a MA curta cruza abaixo da MA longa.
    """
    try:
        data = yf.download(ticker, start=start_date, end=end_date, progress=False)
        if data.empty:
            return {"error": "Não foi possível obter dados históricos para o ticker e período selecionados."}

        df = pd.DataFrame(data['Close'])
        df.columns = ['Close']

        # Calculate Moving Averages
        df['SMA_Short'] = df['Close'].rolling(window=short_ma_period).mean()
        df['SMA_Long'] = df['Close'].rolling(window=long_ma_period).mean()

        # Drop NaN values created by rolling window
        df.dropna(inplace=True)

        if df.empty:
            return {"error": "Dados insuficientes para calcular as médias móveis para o período selecionado. Tente um período maior ou tickers com mais histórico."}

        # Generate Signals
        df['Signal'] = 0 # 0: Hold, 1: Buy, -1: Sell
        
        # When short MA crosses above long MA, generate a buy signal
        df.loc[df['SMA_Short'] > df['SMA_Long'], 'Signal'] = 1
        # When short MA crosses below long MA, generate a sell signal
        df.loc[df['SMA_Short'] < df['SMA_Long'], 'Signal'] = -1

        # Determine position based on signals (state machine)
        df['Position'] = 0 # 0: Out of position, 1: In position
        
        # Use .loc for safe assignment within the loop
        for i in range(1, len(df)):
            if df['Signal'].iloc[i] == 1 and df['Position'].iloc[i-1] == 0: # Buy signal and not in position
                df.loc[df.index[i], 'Position'] = 1
            elif df['Signal'].iloc[i] == -1 and df['Position'].iloc[i-1] == 1: # Sell signal and in position
                df.loc[df.index[i], 'Position'] = 0
            else: # Hold previous position
                df.loc[df.index[i], 'Position'] = df['Position'].iloc[i-1]

        # Identify actual trades (when position changes)
        df['Trade'] = df['Position'].diff()

        # Simulate Trades
        portfolio_value_history = [initial_capital]
        shares_held = 0
        cash = initial_capital
        trades_executed = []
        buy_dates = []
        sell_dates = []

        for i in range(len(df)):
            current_price = df['Close'].iloc[i]
            date = df.index[i]
            trade_signal = df['Trade'].iloc[i]

            if trade_signal == 1: # Buy
                if cash > 0:
                    shares_to_buy = math.floor(cash / current_price)
                    if shares_to_buy > 0:
                        shares_held += shares_to_buy
                        cash -= shares_to_buy * current_price
                        trades_executed.append({'Date': date, 'Type': 'Buy', 'Price': current_price, 'Shares': shares_to_buy})
                        buy_dates.append(date)
            elif trade_signal == -1: # Sell
                if shares_held > 0:
                    cash += shares_held * current_price
                    trades_executed.append({'Date': date, 'Type': 'Sell', 'Price': current_price, 'Shares': shares_held})
                    shares_held = 0
                    sell_dates.append(date)

            current_portfolio_value = cash + (shares_held * current_price)
            portfolio_value_history.append(current_portfolio_value)

        # If still holding shares at the end, sell them (liquidate)
        if shares_held > 0:
            cash += shares_held * df['Close'].iloc[-1]
            trades_executed.append({'Date': df.index[-1], 'Type': 'Sell (Liquidation)', 'Price': df['Close'].iloc[-1], 'Shares': shares_held})
            shares_held = 0
            sell_dates.append(df.index[-1]) # Add last sell date

        final_portfolio_value = cash
        total_return = ((final_portfolio_value - initial_capital) / initial_capital) * 100 if initial_capital > 0 else 0

        portfolio_df = pd.DataFrame({'Portfolio Value': portfolio_value_history[1:]}, index=df.index)

        return {
            "df": df,
            "portfolio_df": portfolio_df,
            "trades_executed": trades_executed,
            "initial_capital": initial_capital,
            "final_portfolio_value": final_portfolio_value,
            "total_return": total_return,
            "num_trades": len(trades_executed),
            "buy_dates": buy_dates,
            "sell_dates": sell_dates
        }
    except Exception as e:
        return {"error": f"Ocorreu um erro inesperado durante o backtest: {e}"}

def pagina_backtesting():
    st.header("🧪 Backtesting de Estratégias")
    st.info("Esta página permite testar estratégias de investimento com base em dados históricos.")

    st.subheader("Configuração da Estratégia (Média Móvel Crossover)")

    col1, col2, col3 = st.columns(3)
    with col1:
        ticker = st.text_input("Ticker do Ativo", value="PETR4.SA", help="Ex: PETR4.SA, AAPL").upper()
    with col2:
        initial_capital = st.number_input("Capital Inicial (R$)", min_value=100.0, value=10000.0, step=100.0)
    with col3:
        short_ma_period = st.number_input("Período da Média Móvel Curta", min_value=5, value=20, step=1)
    
    col4, col5, col6 = st.columns(3)
    with col4:
        long_ma_period = st.number_input("Período da Média Móvel Longa", min_value=10, value=50, step=1)
    with col5:
        start_date = st.date_input("Data de Início", value=datetime(2020, 1, 1), max_value=datetime.now(), format="DD/MM/YYYY")
    with col6:
        end_date = st.date_input("Data de Fim", value=datetime.now(), max_value=datetime.now(), format="DD/MM/YYYY")

    if st.button("Executar Backtest", type="primary", use_container_width=True):
        if short_ma_period >= long_ma_period:
            st.error("O período da Média Móvel Curta deve ser menor que o período da Média Móvel Longa.")
        elif start_date >= end_date:
            st.error("A data de início deve ser anterior à data de fim.")
        else:
            with st.spinner("Executando backtest..."):
                results = run_ma_crossover_backtest(ticker, start_date, end_date, short_ma_period, long_ma_period, initial_capital)

            if "error" in results:
                st.error(f"Erro no Backtest: {results['error']}")
            else:
                st.subheader("Resultados do Backtest")
                col_res1, col_res2, col_res3 = st.columns(3)
                with col_res1:
                    st.metric("Capital Inicial", f"R$ {results['initial_capital']:,.2f}")
                with col_res2:
                    st.metric("Capital Final", f"R$ {results['final_portfolio_value']:,.2f}")
                with col_res3:
                    st.metric("Retorno Total", f"{results['total_return']:,.2f}%")
                
                st.metric("Número de Trades", results['num_trades'])

                st.subheader("Gráfico de Preços e Sinais")
                fig_price = px.line(results['df'], x=results['df'].index, y=['Close', 'SMA_Short', 'SMA_Long'],
                                    title=f"Preço do Ativo e Médias Móveis ({ticker})",
                                    labels={'value': 'Preço (R$)', 'index': 'Data'})
                
                # Add buy signals
                if results['buy_dates']:
                    # Ensure buy_dates are in the DataFrame index
                    valid_buy_dates = [d for d in results['buy_dates'] if d in results['df'].index]
                    if valid_buy_dates:
                        fig_price.add_scatter(x=valid_buy_dates, y=results['df'].loc[valid_buy_dates, 'Close'],
                                            mode='markers', marker=dict(symbol='triangle-up', size=10, color='green'),
                                            name='Compra')
                # Add sell signals
                if results['sell_dates']:
                    # Ensure sell_dates are in the DataFrame index
                    valid_sell_dates = [d for d in results['sell_dates'] if d in results['df'].index]
                    if valid_sell_dates:
                        fig_price.add_scatter(x=valid_sell_dates, y=results['df'].loc[valid_sell_dates, 'Close'],
                                            mode='markers', marker=dict(symbol='triangle-down', size=10, color='red'),
                                            name='Venda')
                
                fig_price.update_layout(hovermode="x unified")
                st.plotly_chart(fig_price, use_container_width=True)

                st.subheader("Evolução do Patrimônio")
                fig_portfolio = px.line(results['portfolio_df'], x=results['portfolio_df'].index, y='Portfolio Value',
                                        title="Evolução do Valor da Carteira",
                                        labels={'value': 'Valor (R$)', 'index': 'Data'})
                fig_portfolio.update_layout(hovermode="x unified")
                st.plotly_chart(fig_portfolio, use_container_width=True)

                st.subheader("Detalhes dos Trades")
                trades_df = pd.DataFrame(results['trades_executed'])
                if not trades_df.empty:
                    st.dataframe(trades_df, use_container_width=True)
                else:
                    st.info("Nenhum trade foi executado durante o período do backtest.")
    st.markdown("""
    <p style='text-align: center; font-size: 0.8em;'>
        <a href='https://github.com/leandrovcorrea' target='_blank' style='color: blue; text-decoration: none; display: inline-flex; align-items: center;'>
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" viewBox="0 0 16 16" style="margin-right: 5px;">
                <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38C13.71 14.53 16 11.54 16 8c0-4.42-3.58-8-8-8"/>
            </svg>
            Criado por Leandrovcorrea
        </a>
    </p>
    """, unsafe_allow_html=True)
 
def main():
    st.set_page_config(page_title="Análise Fundamentalista", layout="wide", page_icon="📊")
    st.markdown('<h1 style="font-size:3rem; text-align: center;">Dinheiro $mart</h1>', unsafe_allow_html=True)
    st.markdown('<h3 style="font-size:1.5rem; text-align: center;">Ferramenta de Análise Fundamentalista e gerenciamento de carteira</h3>', unsafe_allow_html=True)
    
    # Adicione esta linha para depuração
    st.write(f"Versão do Streamlit em execução: {st.__version__}")

    # Inject custom CSS for tab font size
    st.markdown("""
        <style>
        [data-testid="stTabs"] button {
            font-size: 1.1em;
        }
        </style>
    """, unsafe_allow_html=True)
    # Inicialização do estado da sessão
    if 'usuario_logado' not in st.session_state: st.session_state.usuario_logado = None
    if 'auth_page' not in st.session_state: st.session_state.auth_page = "login"
    if 'ticker_analisado' not in st.session_state: st.session_state.ticker_analisado = ""
    if 'active_tab_index' not in st.session_state: st.session_state.active_tab_index = 0

    if st.session_state.usuario_logado is None:
        with st.sidebar:
            st.header("Acesso à Ferramenta")
            if st.session_state.auth_page == "login":
                pagina_login()
            elif st.session_state.auth_page == "criar_conta":
                pagina_criar_conta()
            elif st.session_state.auth_page == "recuperar_senha":
                pagina_recuperar_senha()
        st.info("⬅️ Faça login ou crie uma conta na barra lateral para começar.")
        st.markdown("""
    <p style='text-align: center; font-size: 0.8em;'>
        <a href='https://github.com/leandrovcorrea' target='_blank' style='color: blue; text-decoration: none; display: inline-flex; align-items: center;'>
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" viewBox="0 0 16 16" style="margin-right: 5px;">
                <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38C13.71 14.53 16 11.54 16 8c0-4.42-3.58-8-8-8"/>
            </svg>
            Criado por Leandrovcorrea
        </a>
    </p>
    """, unsafe_allow_html=True)
    else:
        with st.sidebar:
            nome_usuario = st.session_state.usuario_logado['nome']
            st.markdown(f"**{nome_usuario}**")
            if st.button("Sair", use_container_width=True):
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                st.rerun()
        
        tab_names = ["🏠 Dashboard", "💼 Minha Carteira", "👁️ Watchlist", "📊 Análise de Ativos", "🧪 Backtesting"]
 
        tab1, tab2, tab3, tab4, tab5 = st.tabs(tab_names)
 
        with tab1: pagina_dashboard()
        with tab2: pagina_carteira()
        with tab3: pagina_watchlist()
        with tab4: pagina_analise()
        with tab5: pagina_backtesting()
 
    # Seta para voltar ao topo
    st.markdown("""
        <style>
        #voltar-topo-btn {
            position: fixed;
            bottom: 40px;
            right: 40px;
            z-index: 9999;
            background: #2563eb;
            color: white;
            border: none;
            border-radius: 50%;
            width: 56px;
            height: 56px;
            font-size: 2rem;
            box-shadow: 0 2px 8px rgba(0,0,0,0.2);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            opacity: 0;
            visibility: hidden;
            transition: opacity 0.3s ease-in-out, visibility 0s linear 0.3s;
        }
        #voltar-topo-btn.show {
            opacity: 1;
            visibility: visible;
            transition: opacity 0.3s ease-in-out, visibility 0s linear 0s;
        }
        #voltar-topo-btn svg {
            width: 100%;
            height: 100%;
            fill: currentColor;
        }
        </style>
        <button id="voltar-topo-btn" title="Voltar ao topo">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
                <path d="M12 2L2 12h7v10h6V12h7L12 2z"/>
            </svg>
        </button>
        <script>
            const btn = document.getElementById('voltar-topo-btn');
            if (btn) {
                btn.addEventListener('click', function() {
                    window.scrollTo({top: 0, behavior: 'smooth'});
                });
                window.addEventListener('scroll', function() {
                    if (document.documentElement.scrollTop > 200) {
                        btn.classList.add('show');
                    } else {
                        btn.classList.remove('show');
                    }
                });
            }
        </script>
    """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()
