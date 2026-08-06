# Radal — Matriz de Permisos (RBAC)

> **PROPUESTA — validar con el equipo.**
> La matriz que llenó el equipo llegó **en blanco**. Los valores de abajo son una
> **propuesta** derivada de los documentos funcionales y las anclas del FRD, para
> ser revisada y corregida. La fuente de verdad **máquina-legible** es
> [`backend/app/core/roles_config.py`](../backend/app/core/roles_config.py)
> (dict `ROLES`). Cambiar la matriz real = editar **ese único archivo**.

## Convención de valores

| Valor | Significado |
|-------|-------------|
| `si` | Permitido |
| `no` | Denegado |
| `parcial` | Permitido **con restricción** (por scope de vínculo, por campos, o por estado). El gate grueso `require_permission()` lo deja pasar y el router/servicio aplica la restricción fina. Configurable vía `PARTIAL_IS_ALLOWED`. |

## Ejes

- **Macro-perfiles:** `corredora`, `asegurado`, `aseguradora`, `liquidador` (futuro).
- **Sub-roles por macro:**
  - corredora: `admin_corredora`, `ejecutivo_corredora`, `inspector`, `especialista_tecnico`
  - asegurado: `admin_asegurado`, `ejecutivo_asegurado`
  - aseguradora: `admin_aseguradora`, `suscriptor`, `ejecutivo_aseguradora`
  - liquidador (futuro): `admin_liquidador`, `liquidador`
- **Acciones (9):** Ver, Crear, Editar, Eliminar, Comentar, Subir, Enviar, Aprobar, Administrar.
- **Módulos (15):** Dashboard, Clientes, Polizas, Renovaciones, Cotizaciones, Siniestros, Inspecciones, Expediente/DataRoom, PreSuscripcion, OfertasComparacion, Pipeline*, Facturacion*, Reportes*, AdministracionUsuarios, Configuracion.
  - `*` = **FUERA de MVP** → todo `no` para todos los roles.

## Anclas del FRD usadas para fundamentar la propuesta

- `admin_corredora` = acceso total, incluye administración de usuarios.
- `inspector` = solo Inspecciones (Ver/Crear/Editar/Subir/Enviar; **sin** Comentar, **sin** Aprobar). Vista parcial del Expediente para su inspección.
- Roles de **aseguradora** = Ver+Comentar sobre expedientes a los que fueron invitados; Subir/Enviar solo en Ofertas.
- Roles de **asegurado** = Ver+Comentar + Subir antecedentes solo en Siniestros.
- `Eliminar` = generalmente `no` (información se versiona, no se elimina).
- `especialista_tecnico` (nuevo) = apoyo técnico: fuerte en PreSuscripcion / Ofertas / Cotizaciones / Expediente; edición parcial en el resto.
- `Pipeline` / `Facturacion` / `Reportes` = fuera de MVP.

---

## Matriz por rol × módulo (celda = acciones permitidas)

Notación compacta: se listan solo las acciones con `si` o `(parcial)`. Un módulo sin acciones = todo `no`.

### CORREDORA

| Módulo | admin_corredora | ejecutivo_corredora | inspector | especialista_tecnico |
|---|---|---|---|---|
| Dashboard | Ver, Crear, Editar, Comentar, Subir, Enviar, Aprobar, Administrar | Ver, Crear, Comentar | Ver | Ver, Comentar |
| Clientes | todas menos Eliminar | Ver, Crear, Editar, Comentar, Subir, Enviar | — | Ver, (Editar), Comentar, Subir |
| Polizas | todas menos Eliminar | Ver, Crear, Editar, Comentar, Subir, Enviar | — | Ver, (Editar), Comentar, Subir |
| Renovaciones | todas menos Eliminar | Ver, Crear, Editar, Comentar, Subir, Enviar | — | Ver, (Editar), Comentar, Subir |
| Cotizaciones | todas menos Eliminar | Ver, Crear, Editar, Comentar, Subir, Enviar | — | Ver, Crear, Editar, Comentar, Subir, Enviar |
| Siniestros | todas menos Eliminar | Ver, Crear, Editar, Comentar, Subir, Enviar | — | Ver, (Editar), Comentar, Subir |
| Inspecciones | todas menos Eliminar | Ver, Crear, (Editar), Comentar, Subir, Enviar | Ver, Crear, Editar, Subir, Enviar | Ver, Comentar, (Aprobar) |
| Expediente/DataRoom | todas menos Eliminar | Ver, Crear, Editar, Comentar, Subir, Enviar | (Ver), (Subir) | Ver, Crear, Editar, Comentar, Subir, (Enviar) |
| PreSuscripcion | todas menos Eliminar | Ver, Crear, Editar, Comentar, Subir, Enviar | — | Ver, Crear, Editar, Comentar, Subir, Enviar, Aprobar |
| OfertasComparacion | todas menos Eliminar | Ver, Crear, Editar, Comentar, Subir, Enviar | — | Ver, Crear, Editar, Comentar, Subir, Enviar, Aprobar |
| Pipeline* | — | — | — | — |
| Facturacion* | — | — | — | — |
| Reportes* | — | — | — | — |
| AdministracionUsuarios | Ver, Crear, Editar, Enviar, Aprobar, Administrar | — | — | — |
| Configuracion | Ver, Crear, Editar, Subir, Aprobar, Administrar | (Ver) | — | (Ver) |

### ASEGURADO

| Módulo | admin_asegurado | ejecutivo_asegurado |
|---|---|---|
| Dashboard | Ver | Ver |
| Clientes | (Ver), Comentar | (Ver), Comentar |
| Polizas | (Ver), Comentar | (Ver), Comentar |
| Renovaciones | (Ver), Comentar | (Ver), Comentar |
| Cotizaciones | (Ver), Comentar | (Ver), Comentar |
| Siniestros | (Ver), Comentar, Subir | (Ver), Comentar, Subir |
| Inspecciones | (Ver), Comentar | (Ver), Comentar |
| Expediente/DataRoom | (Ver), Comentar | (Ver), Comentar |
| PreSuscripcion | — | — |
| OfertasComparacion | (Ver), Comentar | (Ver), Comentar |
| Pipeline* / Facturacion* / Reportes* | — | — |
| AdministracionUsuarios | Ver, Crear, Editar, Enviar, Aprobar, (Administrar) | — |
| Configuracion | (Ver), (Editar), Subir | — |

### ASEGURADORA

| Módulo | admin_aseguradora | suscriptor | ejecutivo_aseguradora |
|---|---|---|---|
| Dashboard | Ver | Ver | Ver |
| Clientes | (Ver), Comentar | (Ver), Comentar | (Ver), Comentar |
| Polizas | (Ver), Comentar | (Ver), Comentar | (Ver), Comentar |
| Renovaciones | (Ver), Comentar | (Ver), Comentar | (Ver), Comentar |
| Cotizaciones | (Ver), Comentar | (Ver), Comentar | (Ver), Comentar |
| Siniestros | (Ver), Comentar | (Ver), Comentar | (Ver), Comentar |
| Inspecciones | (Ver), Comentar | (Ver), Comentar | (Ver), Comentar |
| Expediente/DataRoom | (Ver), Comentar | (Ver), Comentar | (Ver), Comentar |
| PreSuscripcion | (Ver), Comentar | (Ver), Comentar, Aprobar | (Ver), Comentar |
| OfertasComparacion | Ver, (Crear), (Editar), Comentar, Subir, Enviar, (Aprobar) | Ver, Crear, Editar, Comentar, Subir, Enviar, Aprobar | Ver, Crear, (Editar), Comentar, Subir, Enviar |
| Pipeline* / Facturacion* / Reportes* | — | — | — |
| AdministracionUsuarios | Ver, Crear, Editar, Enviar, Aprobar, (Administrar) | — | — |
| Configuracion | (Ver), (Editar), Subir | — | — |

### LIQUIDADOR (futuro)

| Módulo | admin_liquidador | liquidador |
|---|---|---|
| Dashboard | Ver | Ver |
| Clientes | (Ver), Comentar | (Ver), Comentar |
| Polizas | (Ver), Comentar | (Ver), Comentar |
| Siniestros | Ver, (Editar), Comentar, Subir, Enviar, (Aprobar) | Ver, (Editar), Comentar, Subir, Enviar, (Aprobar) |
| Inspecciones | (Ver), Comentar | (Ver), Comentar |
| Expediente/DataRoom | (Ver), Comentar, (Subir) | (Ver), Comentar, (Subir) |
| Renovaciones / Cotizaciones / PreSuscripcion / OfertasComparacion | — | — |
| Pipeline* / Facturacion* / Reportes* | — | — |
| AdministracionUsuarios | Ver, Crear, Editar, Enviar, Aprobar, (Administrar) | — |
| Configuracion | (Ver), (Editar), Subir | — |

---

## Enforcement

```python
from app.api.deps import require_permission

@router.post("/clientes")
def crear_cliente(user = Depends(require_permission("Clientes", "Crear"))):
    ...
```

`require_permission(module, action)` lee `current_user.subrol`, consulta
`roles_config.is_allowed(subrol, module, action)` y responde `403` si no está
permitido. Para `parcial`, el gate pasa y el router aplica la restricción fina
(p. ej. filtrar por vínculo, limitar campos editables, o exigir estado).

Los guards legacy (`require_roles`, `require_admin_corredora`,
`require_corredora_staff`) siguen funcionando sobre `usuario.rol` — **no se
eliminan**. `require_permission` es aditivo.

## Puntos a validar con el equipo

1. Reemplazar TODA esta propuesta con la matriz real (editar `roles_config.py`).
2. Confirmar el nuevo sub-rol `especialista_tecnico` (no existía en `RolEnum`).
3. Confirmar semántica de `parcial` (¿pasa el gate o falla cerrado?).
4. Confirmar que Pipeline/Facturacion/Reportes quedan fuera de MVP.
5. Confirmar si `admin_asegurado` / `admin_aseguradora` administran usuarios de su propia entidad (`Administrar = parcial`).
