@echo off
title Tunel ngrok - BingX Bot
rem Expoe o backend do bot (porta 8010) no dominio fixo do ngrok, para o painel na Vercel.
rem O backend recusa qualquer chamada sem o token (BOT_API_TOKEN).
ngrok http 8010 --url=https://rewrap-mammogram-economist.ngrok-free.dev
