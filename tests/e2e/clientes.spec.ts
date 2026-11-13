
import { test, expect } from '@playwright/test';

test.describe('Funcionalidade: Clientes', () => {

  test('PW-002 - Consultar cliente por código', async ({ page }) => {

    // Acessar aplicação
    await page.goto('/interface');

    // Autenticação
    const apiToken = process.env.API_TOKEN;

    if (!apiToken) {
      throw new Error('Variável API_TOKEN não configurada.');
    }

    await page.getByRole('textbox', {
      name: 'Chave de acesso'
    }).fill(apiToken);

    await page.getByRole('button', {
      name: 'Entrar'
    }).click();

    // Acessar Clientes
    await page.getByRole('button', {
      name: 'Clientes'
    }).click();

    // Pesquisar cliente
    await page.getByRole('searchbox', {
      name: 'Pesquisar clientes'
    }).fill('CLI-001');

    await page.getByRole('button', {
      name: 'Buscar'
    }).click();

    // Validar resultado
    await expect(
      page.getByText('Mercado Demonstrativo Alfa')
    ).toBeVisible();

    await expect(
      page.getByRole('cell', { name: 'CLI-001', exact: true })
    ).toBeVisible();

    await expect(
      page.getByRole('cell', {
        name: 'cliente001@example.invalid',
        exact: true
      })
    ).toBeVisible();

  });

});
