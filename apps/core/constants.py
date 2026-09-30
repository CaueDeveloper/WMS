# Grupos de Produção/Logística — usuário novo (criado manualmente na tela de
# atribuição do AD ou via primeiro login LDAP) que cai em algum destes nasce
# inativo (is_active=False), exigindo ativação manual por um Administrador
# (ação "Ativar" na tela Usuários) antes de conseguir logar — o AuthenticationForm
# padrão do Django já rejeita login de usuário inativo. Administradores não
# entram nesta lista — continuam ativos desde a criação.
GRUPOS_INATIVOS_POR_PADRAO = {
    'Operador Produção', 'Gestor Produção', 'Operador Logística', 'Gestor Logística',
}
