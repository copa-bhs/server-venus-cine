"""
Script para rodar UMA VEZ após atualizar o sistema.
Ele apaga o banco antigo (se existir) e cria as novas tabelas ricas.
"""
import os
from pathlib import Path
from db import criar_tabelas, DB_PATH, engine, Base

def rodar_migracao():
    print("🚀 Iniciando migração do Banco de Dados...")
    
    # 1. Deleta o banco antigo para evitar conflito de colunas
    if DB_PATH.exists():
        print(f"🗑️  Apagando banco antigo em: {DB_PATH}")
        os.remove(DB_PATH)
    
    # 2. Cria as novas tabelas
    print("🛠️  Criando novo schema...")
    criar_tabelas()
    
    print("✅ Migração concluída! O banco está vazio e pronto para receber o catálogo.")

if __name__ == "__main__":
    rodar_migracao()