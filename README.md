# Centralizador de e-mails Yahoo

Este projeto verifica várias contas Yahoo e copia somente as mensagens novas
para um Gmail central. Ele roda automaticamente às 8h e às 18h no horário de
Fortaleza e mantém um controle de UID para evitar mensagens duplicadas.

## O que o coletor preserva

- conta Yahoo que recebeu a mensagem;
- remetente, destinatário, data e assunto originais;
- conteúdo em texto;
- anexos;
- endereço original no campo `Reply-To`.

## Segurança

Não coloque senhas em arquivos do repositório. Use somente senhas de aplicativo
do Yahoo, cadastradas nos segredos do GitHub. Uma senha de aplicativo pode ser
revogada sem alterar a senha principal da conta.

## Segredos necessários

Em **Settings → Secrets and variables → Actions**, crie:

### `CENTRAL_GMAIL`

O endereço que receberá todas as mensagens:

```text
emailcentral@gmail.com
```

### `YAHOO_ACCOUNTS_JSON`

Uma lista com as contas Yahoo. Exemplo:

```json
[
  {
    "email": "primeiraconta@yahoo.com",
    "app_password": "senha-de-aplicativo",
    "label": "Instituição A"
  },
  {
    "email": "segundaconta@yahoo.com",
    "app_password": "senha-de-aplicativo",
    "label": "Instituição B"
  }
]
```

O campo `label` identifica visualmente a origem no Gmail.

## Primeiro teste

Abra a aba **Actions**, escolha **Coletar emails Yahoo** e clique em
**Run workflow**. Na primeira execução, o coletor considera somente mensagens
dos últimos dois dias. Nas próximas execuções, busca apenas UIDs posteriores ao
último processado.

## Observação

O serviço usa os servidores oficiais IMAP e SMTP do Yahoo. O GitHub não recebe a
senha principal das contas.
