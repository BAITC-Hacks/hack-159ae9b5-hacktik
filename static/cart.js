document.addEventListener('DOMContentLoaded', async () => {
  const target = document.querySelector('#demo-cart-items');
  try {
    const response = await fetch('/api/cart', {credentials: 'same-origin'});
    if (!response.ok) throw new Error('cart');
    const data = await response.json();
    target.replaceChildren();
    if (!data.items.length) { target.textContent = 'Демо-корзина пуста.'; return; }
    let total = 0;
    for (const item of data.items) {
      const row = document.createElement('div');
      row.className = 'cart-row';
      const label = document.createElement('span');
      label.textContent = `${item.name} · ${item.sku} · ${item.quantity} шт.`;
      const amount = document.createElement('b');
      amount.textContent = `${(item.quantity * item.unit_price_kzt).toLocaleString('ru-KZ')} ₸`;
      row.append(label, amount);
      target.append(row);
      total += item.quantity * item.unit_price_kzt;
    }
    const sum = document.createElement('div');
    sum.className = 'cart-row cart-total';
    sum.textContent = `Итого: ${total.toLocaleString('ru-KZ')} ₸`;
    target.append(sum);
  } catch (_) { target.textContent = 'Не удалось загрузить демо-корзину.'; }
});
