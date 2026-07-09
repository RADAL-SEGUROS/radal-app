"""Runnable seeder: python -m app.db.seed

DROPS + CREATES all tables and inserts the RADAL SEGUROS demo dataset so every
module KPI looks real. Dates are fixed relative to the demo "today" 2026-07-08.

KPI targets (from SPEC / api-contract):
  polizas_vigentes = 10, prima_total ~ UF 5.440, suma_asegurada_total ~ UF 65.700,
  con_coaseguro = 3, clientes_activos = 4, renovaciones_activas = 6 (2 negociando,
  0 vencen <=30d, prima_en_juego ~ UF 4.540), cotizaciones en_curso = 4
  (valor_declarado_total ~ UF 36.600, 3 alta prioridad, 2 por vencer L7D).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.core.security import hash_password
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models.activo import Activo, ActivoEstadoEnum
from app.models.asegurado_adicional import AseguradoAdicional
from app.models.aseguradora import Aseguradora
from app.models.cliente import Cliente, ClienteEstadoEnum
from app.models.colaboracion import Actividad, Documento, Observacion
from app.models.corredora import Corredora
from app.models.cotizacion import Cotizacion, CotizacionEstadoEnum, PrioridadEnum
from app.models.inspeccion import (
    Inspeccion,
    InspeccionEstadoEnum,
    SolicitudInspeccion,
    SolicitudInspeccionEstadoEnum,
)
from app.models.oferta import Oferta, OfertaEstadoEnum
from app.models.poliza import (
    CoaseguroParticipacion,
    CoberturaItem,
    CoberturaItemTipoEnum,
    Poliza,
    PolizaEstadoEnum,
    TipoCoberturaEnum,
    UbicacionPoliza,
)
from app.models.proceso_ramo import ProcesoRamo, ProcesoRamoEstadoEnum
from app.models.ramo import Ramo
from app.models.renovacion import Renovacion, RenovacionEstadoEnum
from app.models.siniestro import Siniestro, SiniestroEstadoEnum
from app.models.usuario import RolEnum, Usuario

# Demo "today" — everything is anchored here.
TODAY = date(2026, 7, 8)


def _dt(d: date, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=timezone.utc)


def _days(n: int) -> date:
    return TODAY + timedelta(days=n)


def reset_schema() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def seed() -> dict[str, int]:
    reset_schema()
    counts: dict[str, int] = {}

    with SessionLocal() as db:
        # --- Corredora (tenant) -------------------------------------------
        corredora = Corredora(
            nombre="RADAL SEGUROS",
            rut="76.123.456-7",
            logo_url=None,
            created_at=_dt(date(2024, 1, 1)),
        )
        db.add(corredora)
        db.flush()
        cid = corredora.id
        counts["corredora"] = 1

        # --- Usuarios ------------------------------------------------------
        pw = hash_password("radal1234")
        jose = Usuario(
            corredora_id=cid,
            nombre="José Pérez",
            email="jose@radalseguros.cl",
            hashed_password=pw,
            cargo="Ejecutivo Comercial",
            rol=RolEnum.ejecutivo_corredora,
            activo=True,
            created_at=_dt(date(2024, 1, 5)),
        )
        admin = Usuario(
            corredora_id=cid,
            nombre="Admin Radal",
            email="admin@radalseguros.cl",
            hashed_password=pw,
            cargo="Gerente",
            rol=RolEnum.admin_corredora,
            activo=True,
            created_at=_dt(date(2024, 1, 5)),
        )
        ben = Usuario(
            corredora_id=cid,
            nombre="Ben",
            email="ben@nirvana-ai.com",
            hashed_password=pw,
            cargo="Owner",
            rol=RolEnum.admin_corredora,
            activo=True,
            created_at=_dt(date(2024, 1, 5)),
        )
        # An inspector to own inspecciones.
        inspector = Usuario(
            corredora_id=cid,
            nombre="Carla Muñoz",
            email="carla@radalseguros.cl",
            hashed_password=pw,
            cargo="Inspectora",
            rol=RolEnum.inspector,
            activo=True,
            created_at=_dt(date(2024, 1, 6)),
        )
        db.add_all([jose, admin, ben, inspector])
        db.flush()
        counts["usuario"] = 4

        # --- Aseguradoras --------------------------------------------------
        chubb = Aseguradora(corredora_id=cid, nombre="Chubb Generales", rut="99.500.001-1",
                            sitio_pago_url="https://pago.chubb.cl")
        hdi = Aseguradora(corredora_id=cid, nombre="HDI Seguros", rut="99.500.002-2",
                          sitio_pago_url="https://pago.hdi.cl")
        mapfre = Aseguradora(corredora_id=cid, nombre="Mapfre Generales", rut="99.500.003-3",
                             sitio_pago_url="https://pago.mapfre.cl")
        liberty = Aseguradora(corredora_id=cid, nombre="Liberty Seguros", rut="99.500.004-4",
                              sitio_pago_url="https://pago.liberty.cl")
        db.add_all([chubb, hdi, mapfre, liberty])
        db.flush()
        counts["aseguradora"] = 4

        # --- Ramos ---------------------------------------------------------
        incendio_checklist_campos = {
            "campos": ["construccion", "uso", "superficie_m2", "sistema_incendio"],
        }
        ramo_incendio = Ramo(
            corredora_id=cid,
            nombre="Incendio/Sismo",
            requiere_inspeccion=True,
            campos_min={"required": ["direccion", "suma_asegurada", "construccion"]},
            reglas_presuscripcion={"max_suma_sin_inspeccion_uf": 3000},
            campos_comparador=incendio_checklist_campos,
        )
        ramo_rc = Ramo(
            corredora_id=cid,
            nombre="Responsabilidad Civil",
            requiere_inspeccion=False,
            campos_min={"required": ["giro", "limite"]},
            reglas_presuscripcion={},
            campos_comparador={"campos": ["limite", "deducible"]},
        )
        ramo_transporte = Ramo(
            corredora_id=cid,
            nombre="Transporte",
            requiere_inspeccion=False,
            campos_min={"required": ["tipo_carga", "trayecto", "valor_declarado"]},
            reglas_presuscripcion={},
            campos_comparador={"campos": ["prima", "deducible", "coberturas"]},
        )
        ramo_flota = Ramo(
            corredora_id=cid,
            nombre="Flota de Vehículos",
            requiere_inspeccion=False,
            campos_min={"required": ["cantidad_vehiculos", "uso"]},
            reglas_presuscripcion={},
            campos_comparador={"campos": ["prima", "deducible"]},
        )
        ramo_cyber = Ramo(
            corredora_id=cid,
            nombre="Cyber",
            requiere_inspeccion=False,
            campos_min={"required": ["facturacion_anual", "sector"]},
            reglas_presuscripcion={},
            campos_comparador={"campos": ["limite", "retroactividad"]},
        )
        db.add_all([ramo_incendio, ramo_rc, ramo_transporte, ramo_flota, ramo_cyber])
        db.flush()
        counts["ramo"] = 5

        # --- Clientes ------------------------------------------------------
        robles = Cliente(
            corredora_id=cid,
            nombre="Agrícola Los Robles S.A.",
            rut="77.888.111-1",
            sector="Agroindustria",
            estado=ClienteEstadoEnum.activo,
            contacto_principal="María Fernández",
            telefono="+56 9 8123 4567",
            email="contacto@losrobles.cl",
            ejecutivo_id=jose.id,
            fecha_alta=date(2024, 1, 10),
            created_at=_dt(date(2024, 1, 10)),
        )
        andes = Cliente(
            corredora_id=cid,
            nombre="Constructora Andes Ltda.",
            rut="77.888.222-2",
            sector="Construcción",
            estado=ClienteEstadoEnum.activo,
            contacto_principal="Rodrigo Soto",
            telefono="+56 9 8222 1111",
            email="rsoto@andes.cl",
            ejecutivo_id=jose.id,
            fecha_alta=date(2024, 3, 2),
            created_at=_dt(date(2024, 3, 2)),
        )
        pacifico = Cliente(
            corredora_id=cid,
            nombre="Transportes Pacífico S.A.",
            rut="77.888.333-3",
            sector="Logística",
            estado=ClienteEstadoEnum.activo,
            contacto_principal="Ignacio Rivas",
            telefono="+56 9 8333 2222",
            email="irivas@pacifico.cl",
            ejecutivo_id=admin.id,
            fecha_alta=date(2024, 5, 20),
            created_at=_dt(date(2024, 5, 20)),
        )
        vina = Cliente(
            corredora_id=cid,
            nombre="Viña del Valle SpA",
            rut="77.888.444-4",
            sector="Vitivinícola",
            estado=ClienteEstadoEnum.activo,
            contacto_principal="Paula Gómez",
            telefono="+56 9 8444 3333",
            email="pgomez@vinadelvalle.cl",
            ejecutivo_id=jose.id,
            fecha_alta=date(2024, 6, 15),
            created_at=_dt(date(2024, 6, 15)),
        )
        techlab = Cliente(
            corredora_id=cid,
            nombre="TechLab Innovaciones SpA",
            rut="77.888.555-5",
            sector="Tecnología",
            estado=ClienteEstadoEnum.onboarding,
            contacto_principal="Daniel Araya",
            telefono="+56 9 8555 4444",
            email="daniel@techlab.cl",
            ejecutivo_id=jose.id,
            fecha_alta=date(2026, 6, 1),
            created_at=_dt(date(2026, 6, 1)),
        )
        minera = Cliente(
            corredora_id=cid,
            nombre="Minera Cordillera Ltda.",
            rut="77.888.666-6",
            sector="Minería",
            estado=ClienteEstadoEnum.prospecto,
            contacto_principal="Verónica Lillo",
            telefono="+56 9 8666 5555",
            email="vlillo@cordillera.cl",
            ejecutivo_id=admin.id,
            fecha_alta=date(2026, 6, 25),
            created_at=_dt(date(2026, 6, 25)),
        )
        db.add_all([robles, andes, pacifico, vina, techlab, minera])
        db.flush()
        counts["cliente"] = 6

        # --- Activos -------------------------------------------------------
        planta_talca = Activo(
            corredora_id=cid, cliente_id=robles.id, tipo_activo="planta",
            nombre="Planta Talca", direccion="Camino Longitudinal Km 8, Talca",
            atributos={"construccion": "Hormigón", "superficie_m2": 4200},
            estado=ActivoEstadoEnum.activo,
        )
        edificio_admin = Activo(
            corredora_id=cid, cliente_id=robles.id, tipo_activo="edificio",
            nombre="Edificio administrativo Los Robles",
            direccion="Av. Circunvalación 220, Talca",
            atributos={"construccion": "Albañilería", "pisos": 3},
            estado=ActivoEstadoEnum.activo,
        )
        obra_andes = Activo(
            corredora_id=cid, cliente_id=andes.id, tipo_activo="obra",
            nombre="Obra Edificio Mirador", direccion="Av. Kennedy 4500, Las Condes",
            atributos={"etapa": "estructura"}, estado=ActivoEstadoEnum.activo,
        )
        flota_pacifico = Activo(
            corredora_id=cid, cliente_id=pacifico.id, tipo_activo="flota",
            nombre="Flota refrigerada Pacífico", direccion="Ruta 5 Sur, San Bernardo",
            atributos={"vehiculos": 24}, estado=ActivoEstadoEnum.activo,
        )
        bodega_vina = Activo(
            corredora_id=cid, cliente_id=vina.id, tipo_activo="bodega",
            nombre="Bodega de guarda Viña del Valle", direccion="Fundo El Valle, Curicó",
            atributos={"barricas": 1800}, estado=ActivoEstadoEnum.activo,
        )
        datacenter_tech = Activo(
            corredora_id=cid, cliente_id=techlab.id, tipo_activo="infraestructura_ti",
            nombre="Datacenter TechLab", direccion="Av. Providencia 1200, Santiago",
            atributos={"racks": 20}, estado=ActivoEstadoEnum.en_evaluacion,
        )
        db.add_all([planta_talca, edificio_admin, obra_andes, flota_pacifico,
                    bodega_vina, datacenter_tech])
        db.flush()
        counts["activo"] = 6

        # --- Asegurados adicionales ---------------------------------------
        aa1 = AseguradoAdicional(
            corredora_id=cid, cliente_id=robles.id, poliza_id=None,
            rut="97.000.000-1", entidad="Banco de Chile", tipo_seguro="Incendio",
            relacion_bien="Acreedor prendario - Maquinaria",
        )
        aa2 = AseguradoAdicional(
            corredora_id=cid, cliente_id=pacifico.id, poliza_id=None,
            rut="97.000.000-2", entidad="Leasing Andino", tipo_seguro="Flota",
            relacion_bien="Leasing de vehículos",
        )
        db.add_all([aa1, aa2])
        db.flush()
        counts["asegurado_adicional"] = 2

        # --- Proceso ramo (flagship) --------------------------------------
        proc_robles = ProcesoRamo(
            corredora_id=cid, activo_id=planta_talca.id, ramo_id=ramo_incendio.id,
            periodo="2024", vigencia_objetivo=date(2024, 6, 1),
            estado=ProcesoRamoEstadoEnum.asegurado,
        )
        proc_tech = ProcesoRamo(
            corredora_id=cid, activo_id=datacenter_tech.id, ramo_id=ramo_cyber.id,
            periodo="2026", vigencia_objetivo=_days(30),
            estado=ProcesoRamoEstadoEnum.en_cotizacion,
        )
        db.add_all([proc_robles, proc_tech])
        db.flush()
        counts["proceso_ramo"] = 2

        # --- Pólizas (10 vigentes) ----------------------------------------
        # Flagship POL-2024-0072 — coaseguro, prima 900, suma 9000.
        pol_flag = Poliza(
            corredora_id=cid, cliente_id=robles.id, activo_id=planta_talca.id,
            proceso_ramo_id=proc_robles.id, numero_poliza="POL-2024-0072",
            ramo_id=ramo_incendio.id, aseguradora_id=chubb.id, tiene_coaseguro=True,
            prima=900, comision_pct=15, suma_asegurada=9000,
            tipo_cobertura=TipoCoberturaEnum.optima, cobertura_pct=100,
            vigencia_inicio=date(2024, 6, 1), vigencia_fin=date(2025, 6, 1),
            estado=PolizaEstadoEnum.vigente, deducible_texto="2% mín UF 30",
            limite_indemnizacion=9000, plan_pago_cuotas=6,
            plan_pago_metodo="Transferencia", pago_url="https://pago.chubb.cl/POL-2024-0072",
        )
        db.add(pol_flag)
        db.flush()

        db.add_all([
            CoaseguroParticipacion(poliza_id=pol_flag.id, aseguradora_id=chubb.id,
                                   es_lider=True, porcentaje=60),
            CoaseguroParticipacion(poliza_id=pol_flag.id, aseguradora_id=hdi.id,
                                   es_lider=False, porcentaje=25),
            CoaseguroParticipacion(poliza_id=pol_flag.id, aseguradora_id=mapfre.id,
                                   es_lider=False, porcentaje=15),
        ])
        db.add_all([
            UbicacionPoliza(poliza_id=pol_flag.id, nombre="Planta Talca - Maquinaria",
                            direccion="Camino Longitudinal Km 8, Talca",
                            suma_asegurada=6500, porcentaje=72),
            UbicacionPoliza(poliza_id=pol_flag.id, nombre="Edificio administrativo",
                            direccion="Av. Circunvalación 220, Talca",
                            suma_asegurada=2500, porcentaje=28),
        ])
        db.add_all([
            CoberturaItem(poliza_id=pol_flag.id, tipo=CoberturaItemTipoEnum.cobertura,
                          descripcion="Todo riesgo daño físico directo"),
            CoberturaItem(poliza_id=pol_flag.id, tipo=CoberturaItemTipoEnum.cobertura,
                          descripcion="Pérdida o daño maquinaria"),
            CoberturaItem(poliza_id=pol_flag.id, tipo=CoberturaItemTipoEnum.cobertura,
                          descripcion="Equipos electrónicos en planta"),
            CoberturaItem(poliza_id=pol_flag.id, tipo=CoberturaItemTipoEnum.exclusion,
                          descripcion="Vicio propio"),
            CoberturaItem(poliza_id=pol_flag.id, tipo=CoberturaItemTipoEnum.exclusion,
                          descripcion="Desgaste"),
            CoberturaItem(poliza_id=pol_flag.id, tipo=CoberturaItemTipoEnum.exclusion,
                          descripcion="Pruebas y ensayos no autorizados"),
        ])

        # Remaining 9 pólizas. primas chosen to total 5440 with flagship (900).
        # 900 + 620 + 540 + 480 + 760 + 410 + 350 + 610 + 470 + 300 = 5440
        # suma total target ~65700: 9000+8200+7600+6900+9800+5400+3800+7200+4600+3200 = 65700
        # 3 con coaseguro total -> flagship + 2 more below.
        pol2 = Poliza(
            corredora_id=cid, cliente_id=robles.id, activo_id=edificio_admin.id,
            proceso_ramo_id=None, numero_poliza="POL-2024-0081",
            ramo_id=ramo_rc.id, aseguradora_id=liberty.id, tiene_coaseguro=False,
            prima=620, comision_pct=12, suma_asegurada=8200,
            tipo_cobertura=TipoCoberturaEnum.optima, cobertura_pct=98,
            vigencia_inicio=date(2024, 7, 1), vigencia_fin=date(2025, 7, 1),
            estado=PolizaEstadoEnum.vigente, deducible_texto="UF 20",
            limite_indemnizacion=8200, plan_pago_cuotas=4,
            plan_pago_metodo="Transferencia", pago_url=None,
        )
        pol3 = Poliza(
            corredora_id=cid, cliente_id=andes.id, activo_id=obra_andes.id,
            proceso_ramo_id=None, numero_poliza="POL-2024-0090",
            ramo_id=ramo_incendio.id, aseguradora_id=hdi.id, tiene_coaseguro=True,
            prima=540, comision_pct=14, suma_asegurada=7600,
            tipo_cobertura=TipoCoberturaEnum.infravalorada, cobertura_pct=88,
            vigencia_inicio=date(2024, 8, 15), vigencia_fin=date(2025, 8, 15),
            estado=PolizaEstadoEnum.vigente, deducible_texto="3% mín UF 40",
            limite_indemnizacion=7600, plan_pago_cuotas=6,
            plan_pago_metodo="Transferencia", pago_url=None,
        )
        pol4 = Poliza(
            corredora_id=cid, cliente_id=andes.id, activo_id=obra_andes.id,
            proceso_ramo_id=None, numero_poliza="POL-2024-0104",
            ramo_id=ramo_rc.id, aseguradora_id=mapfre.id, tiene_coaseguro=False,
            prima=480, comision_pct=10, suma_asegurada=6900,
            tipo_cobertura=TipoCoberturaEnum.optima, cobertura_pct=101,
            vigencia_inicio=date(2024, 9, 1), vigencia_fin=date(2025, 9, 1),
            estado=PolizaEstadoEnum.vigente, deducible_texto="UF 25",
            limite_indemnizacion=6900, plan_pago_cuotas=3,
            plan_pago_metodo="Tarjeta", pago_url=None,
        )
        pol5 = Poliza(
            corredora_id=cid, cliente_id=pacifico.id, activo_id=flota_pacifico.id,
            proceso_ramo_id=None, numero_poliza="POL-2024-0112",
            ramo_id=ramo_flota.id, aseguradora_id=chubb.id, tiene_coaseguro=False,
            prima=760, comision_pct=13, suma_asegurada=9800,
            tipo_cobertura=TipoCoberturaEnum.optima, cobertura_pct=100,
            vigencia_inicio=date(2024, 10, 1), vigencia_fin=date(2025, 10, 1),
            estado=PolizaEstadoEnum.vigente, deducible_texto="UF 15 por evento",
            limite_indemnizacion=9800, plan_pago_cuotas=12,
            plan_pago_metodo="Transferencia", pago_url=None,
        )
        pol6 = Poliza(
            corredora_id=cid, cliente_id=pacifico.id, activo_id=flota_pacifico.id,
            proceso_ramo_id=None, numero_poliza="POL-2024-0125",
            ramo_id=ramo_transporte.id, aseguradora_id=hdi.id, tiene_coaseguro=False,
            prima=410, comision_pct=11, suma_asegurada=5400,
            tipo_cobertura=TipoCoberturaEnum.sobrevalorada, cobertura_pct=112,
            vigencia_inicio=date(2024, 11, 1), vigencia_fin=date(2025, 11, 1),
            estado=PolizaEstadoEnum.vigente, deducible_texto="10% mín UF 10",
            limite_indemnizacion=5400, plan_pago_cuotas=2,
            plan_pago_metodo="Transferencia", pago_url=None,
        )
        pol7 = Poliza(
            corredora_id=cid, cliente_id=vina.id, activo_id=bodega_vina.id,
            proceso_ramo_id=None, numero_poliza="POL-2025-0007",
            ramo_id=ramo_incendio.id, aseguradora_id=mapfre.id, tiene_coaseguro=False,
            prima=350, comision_pct=12, suma_asegurada=3800,
            tipo_cobertura=TipoCoberturaEnum.optima, cobertura_pct=97,
            vigencia_inicio=date(2025, 1, 15), vigencia_fin=date(2026, 1, 15),
            estado=PolizaEstadoEnum.vigente, deducible_texto="2% mín UF 25",
            limite_indemnizacion=3800, plan_pago_cuotas=4,
            plan_pago_metodo="Transferencia", pago_url=None,
        )
        pol8 = Poliza(
            corredora_id=cid, cliente_id=vina.id, activo_id=bodega_vina.id,
            proceso_ramo_id=None, numero_poliza="POL-2025-0019",
            ramo_id=ramo_transporte.id, aseguradora_id=liberty.id, tiene_coaseguro=True,
            prima=610, comision_pct=13, suma_asegurada=7200,
            tipo_cobertura=TipoCoberturaEnum.optima, cobertura_pct=103,
            vigencia_inicio=date(2025, 2, 1), vigencia_fin=date(2026, 2, 1),
            estado=PolizaEstadoEnum.vigente, deducible_texto="UF 12",
            limite_indemnizacion=7200, plan_pago_cuotas=6,
            plan_pago_metodo="Transferencia", pago_url=None,
        )
        pol9 = Poliza(
            corredora_id=cid, cliente_id=robles.id, activo_id=planta_talca.id,
            proceso_ramo_id=None, numero_poliza="POL-2025-0033",
            ramo_id=ramo_flota.id, aseguradora_id=hdi.id, tiene_coaseguro=False,
            prima=470, comision_pct=11, suma_asegurada=4600,
            tipo_cobertura=TipoCoberturaEnum.optima, cobertura_pct=99,
            vigencia_inicio=date(2025, 3, 1), vigencia_fin=date(2026, 3, 1),
            estado=PolizaEstadoEnum.vigente, deducible_texto="UF 10",
            limite_indemnizacion=4600, plan_pago_cuotas=4,
            plan_pago_metodo="Transferencia", pago_url=None,
        )
        pol10 = Poliza(
            corredora_id=cid, cliente_id=andes.id, activo_id=obra_andes.id,
            proceso_ramo_id=None, numero_poliza="POL-2025-0048",
            ramo_id=ramo_rc.id, aseguradora_id=chubb.id, tiene_coaseguro=False,
            prima=300, comision_pct=10, suma_asegurada=3200,
            tipo_cobertura=TipoCoberturaEnum.optima, cobertura_pct=100,
            vigencia_inicio=date(2025, 4, 1), vigencia_fin=date(2026, 4, 1),
            estado=PolizaEstadoEnum.vigente, deducible_texto="UF 18",
            limite_indemnizacion=3200, plan_pago_cuotas=3,
            plan_pago_metodo="Transferencia", pago_url=None,
        )
        # An extra non-vigente póliza (does not count toward the 10 vigentes KPI).
        pol_old = Poliza(
            corredora_id=cid, cliente_id=robles.id, activo_id=edificio_admin.id,
            proceso_ramo_id=None, numero_poliza="POL-2023-0301",
            ramo_id=ramo_rc.id, aseguradora_id=liberty.id, tiene_coaseguro=False,
            prima=280, comision_pct=10, suma_asegurada=3000,
            tipo_cobertura=TipoCoberturaEnum.optima, cobertura_pct=100,
            vigencia_inicio=date(2023, 6, 1), vigencia_fin=date(2024, 6, 1),
            estado=PolizaEstadoEnum.no_vigente, deducible_texto="UF 15",
            limite_indemnizacion=3000, plan_pago_cuotas=2,
            plan_pago_metodo="Transferencia", pago_url=None,
        )
        db.add_all([pol2, pol3, pol4, pol5, pol6, pol7, pol8, pol9, pol10, pol_old])
        db.flush()

        # coaseguro splits for the other two coaseguro pólizas
        db.add_all([
            CoaseguroParticipacion(poliza_id=pol3.id, aseguradora_id=hdi.id,
                                   es_lider=True, porcentaje=70),
            CoaseguroParticipacion(poliza_id=pol3.id, aseguradora_id=liberty.id,
                                   es_lider=False, porcentaje=30),
            CoaseguroParticipacion(poliza_id=pol8.id, aseguradora_id=liberty.id,
                                   es_lider=True, porcentaje=55),
            CoaseguroParticipacion(poliza_id=pol8.id, aseguradora_id=mapfre.id,
                                   es_lider=False, porcentaje=45),
        ])
        # A couple of coberturas on secondary pólizas for detail richness.
        db.add_all([
            CoberturaItem(poliza_id=pol5.id, tipo=CoberturaItemTipoEnum.cobertura,
                          descripcion="Daño material a vehículos"),
            CoberturaItem(poliza_id=pol5.id, tipo=CoberturaItemTipoEnum.cobertura,
                          descripcion="Responsabilidad civil frente a terceros"),
            CoberturaItem(poliza_id=pol5.id, tipo=CoberturaItemTipoEnum.exclusion,
                          descripcion="Conducción bajo influencia"),
        ])
        db.flush()
        counts["poliza"] = 11  # 10 vigentes + 1 no_vigente
        counts["poliza_vigente"] = 10
        counts["coaseguro_participacion"] = 3 + 2 + 2
        counts["ubicacion_poliza"] = 2
        counts["cobertura_item"] = 6 + 3

        # link the flagship banco asegurado adicional to the flagship póliza
        aa1.poliza_id = pol_flag.id
        db.flush()

        # --- Renovaciones (6 activas) -------------------------------------
        # prima_defender sums to ~4540: 900+820+760+700+680+680 = 4540
        # fecha_vencimiento all > 30 days out (0 por_vencer_30d). 2 negociando.
        renovaciones = [
            Renovacion(
                corredora_id=cid, poliza_id=pol_flag.id, cliente_id=robles.id,
                ramo_id=ramo_incendio.id, aseguradora_id=chubb.id, aplica_coaseguro=True,
                prima_defender=900, comision_pct=15, fecha_vencimiento=_days(43),
                ejecutivo_id=jose.id, estado=RenovacionEstadoEnum.cotizando,
                estado_negociacion_texto="A la espera de oferta de HDI para coaseguro.",
            ),
            Renovacion(
                corredora_id=cid, poliza_id=pol2.id, cliente_id=robles.id,
                ramo_id=ramo_rc.id, aseguradora_id=liberty.id, aplica_coaseguro=False,
                prima_defender=820, comision_pct=12, fecha_vencimiento=_days(58),
                ejecutivo_id=jose.id, estado=RenovacionEstadoEnum.negociando,
                estado_negociacion_texto="Negociando reducción de deducible.",
            ),
            Renovacion(
                corredora_id=cid, poliza_id=pol5.id, cliente_id=pacifico.id,
                ramo_id=ramo_flota.id, aseguradora_id=chubb.id, aplica_coaseguro=False,
                prima_defender=760, comision_pct=13, fecha_vencimiento=_days(88),
                ejecutivo_id=admin.id, estado=RenovacionEstadoEnum.cotizando,
                estado_negociacion_texto=None,
            ),
            Renovacion(
                corredora_id=cid, poliza_id=pol3.id, cliente_id=andes.id,
                ramo_id=ramo_incendio.id, aseguradora_id=hdi.id, aplica_coaseguro=True,
                prima_defender=700, comision_pct=14, fecha_vencimiento=_days(120),
                ejecutivo_id=jose.id, estado=RenovacionEstadoEnum.por_iniciar,
                estado_negociacion_texto=None,
            ),
            Renovacion(
                corredora_id=cid, poliza_id=pol8.id, cliente_id=vina.id,
                ramo_id=ramo_transporte.id, aseguradora_id=liberty.id, aplica_coaseguro=True,
                prima_defender=680, comision_pct=13, fecha_vencimiento=_days(45),
                ejecutivo_id=jose.id, estado=RenovacionEstadoEnum.negociando,
                estado_negociacion_texto="Cliente evalúa aumentar suma asegurada.",
            ),
            Renovacion(
                corredora_id=cid, poliza_id=pol6.id, cliente_id=pacifico.id,
                ramo_id=ramo_transporte.id, aseguradora_id=hdi.id, aplica_coaseguro=False,
                prima_defender=680, comision_pct=11, fecha_vencimiento=_days(100),
                ejecutivo_id=admin.id, estado=RenovacionEstadoEnum.por_iniciar,
                estado_negociacion_texto=None,
            ),
        ]
        db.add_all(renovaciones)
        db.flush()
        counts["renovacion"] = 6

        # --- Cotizaciones (4 en curso) ------------------------------------
        # valor_declarado total ~36600: 12000 + 9800 + 8600 + 6200 = 36600
        # 3 alta prioridad; 2 por vencer L7D (dias_restantes < 7).
        cot1 = Cotizacion(
            corredora_id=cid, cliente_id=pacifico.id, activo_id=flota_pacifico.id,
            ramo_id=ramo_transporte.id, bien_asegurar="Carga refrigerada exportación",
            valor_declarado=12000, fecha_envio=_days(-6), fecha_vence=_days(3),
            prioridad=PrioridadEnum.alta, estado=CotizacionEstadoEnum.pendiente,
        )
        cot2 = Cotizacion(
            corredora_id=cid, cliente_id=techlab.id, activo_id=datacenter_tech.id,
            ramo_id=ramo_cyber.id, bien_asegurar="Cobertura Cyber datacenter",
            valor_declarado=9800, fecha_envio=_days(-4), fecha_vence=_days(5),
            prioridad=PrioridadEnum.alta, estado=CotizacionEstadoEnum.pendiente,
        )
        cot3 = Cotizacion(
            corredora_id=cid, cliente_id=vina.id, activo_id=bodega_vina.id,
            ramo_id=ramo_incendio.id, bien_asegurar="Bodega de guarda barricas",
            valor_declarado=8600, fecha_envio=_days(-10), fecha_vence=_days(20),
            prioridad=PrioridadEnum.alta, estado=CotizacionEstadoEnum.respondida,
        )
        cot4 = Cotizacion(
            corredora_id=cid, cliente_id=minera.id, activo_id=None,
            ramo_id=ramo_rc.id, bien_asegurar="Responsabilidad civil operaciones mineras",
            valor_declarado=6200, fecha_envio=_days(-12), fecha_vence=_days(35),
            prioridad=PrioridadEnum.media, estado=CotizacionEstadoEnum.pendiente,
        )
        db.add_all([cot1, cot2, cot3, cot4])
        db.flush()
        counts["cotizacion"] = 4

        # --- Ofertas (comparator on the respondida cotización) ------------
        ofertas = [
            Oferta(
                corredora_id=cid, cotizacion_id=cot3.id, proceso_ramo_id=None,
                aseguradora_id=mapfre.id, prima=340, deducible="2% mín UF 25",
                coberturas=["Incendio", "Sismo", "Daño por agua"],
                exclusiones=["Vicio propio"], vigencia="12 meses",
                estado=OfertaEstadoEnum.enviada,
            ),
            Oferta(
                corredora_id=cid, cotizacion_id=cot3.id, proceso_ramo_id=None,
                aseguradora_id=chubb.id, prima=365, deducible="2% mín UF 30",
                coberturas=["Incendio", "Sismo", "Daño por agua", "Robo"],
                exclusiones=["Vicio propio", "Desgaste"], vigencia="12 meses",
                estado=OfertaEstadoEnum.enviada,
            ),
            Oferta(
                corredora_id=cid, cotizacion_id=cot2.id, proceso_ramo_id=proc_tech.id,
                aseguradora_id=hdi.id, prima=520, deducible="UF 40",
                coberturas=["Brecha de datos", "Interrupción de negocio"],
                exclusiones=["Actos de guerra"], vigencia="12 meses",
                estado=OfertaEstadoEnum.borrador,
            ),
        ]
        db.add_all(ofertas)
        db.flush()
        counts["oferta"] = 3

        # --- Siniestros (3 across states) ---------------------------------
        sin1 = Siniestro(
            corredora_id=cid, poliza_id=pol_flag.id, cliente_id=robles.id,
            activo_id=planta_talca.id, fecha_evento=_days(-57), tipo="Incendio",
            descripcion="Amago de incendio en sector de maquinaria; daño parcial.",
            estado=SiniestroEstadoEnum.en_evaluacion, monto_estimado=1200,
            monto_liquidado=None, fecha_liquidacion=None,
        )
        sin2 = Siniestro(
            corredora_id=cid, poliza_id=pol5.id, cliente_id=pacifico.id,
            activo_id=flota_pacifico.id, fecha_evento=_days(-25), tipo="Colisión",
            descripcion="Colisión de camión refrigerado en ruta.",
            estado=SiniestroEstadoEnum.en_documentacion, monto_estimado=800,
            monto_liquidado=None, fecha_liquidacion=None,
        )
        sin3 = Siniestro(
            corredora_id=cid, poliza_id=pol7.id, cliente_id=vina.id,
            activo_id=bodega_vina.id, fecha_evento=_days(-95), tipo="Daño por agua",
            descripcion="Filtración en bodega de guarda; liquidado.",
            estado=SiniestroEstadoEnum.liquidado, monto_estimado=300,
            monto_liquidado=280, fecha_liquidacion=_days(-30),
        )
        db.add_all([sin1, sin2, sin3])
        db.flush()
        counts["siniestro"] = 3

        # --- Inspecciones -------------------------------------------------
        sol1 = SolicitudInspeccion(
            corredora_id=cid, activo_id=planta_talca.id, proceso_ramo_id=proc_robles.id,
            motivo="Renovación Incendio/Sismo — verificación de sistema contra incendios",
            urgencia="media", fecha_objetivo=_days(15),
            estado=SolicitudInspeccionEstadoEnum.asignada, created_by=jose.id,
        )
        sol2 = SolicitudInspeccion(
            corredora_id=cid, activo_id=bodega_vina.id, proceso_ramo_id=None,
            motivo="Evaluación de riesgo bodega de guarda",
            urgencia="baja", fecha_objetivo=_days(25),
            estado=SolicitudInspeccionEstadoEnum.solicitada, created_by=admin.id,
        )
        db.add_all([sol1, sol2])
        db.flush()

        incendio_checklist = {
            "titulo": "Checklist Incendio/Sismo",
            "items": [
                {"pregunta": "¿Sistema de detección de incendios operativo?", "respuesta": "si"},
                {"pregunta": "¿Extintores vigentes y señalizados?", "respuesta": "si"},
                {"pregunta": "¿Red húmeda / seca disponible?", "respuesta": "parcial"},
                {"pregunta": "¿Instalación eléctrica certificada?", "respuesta": "si"},
                {"pregunta": "¿Almacenamiento de inflamables segregado?", "respuesta": "pendiente"},
            ],
        }
        insp1 = Inspeccion(
            corredora_id=cid, activo_id=planta_talca.id, solicitud_id=sol1.id,
            inspector_id=inspector.id, version=1,
            estado=InspeccionEstadoEnum.en_progreso, checklist=incendio_checklist,
        )
        insp2 = Inspeccion(
            corredora_id=cid, activo_id=bodega_vina.id, solicitud_id=None,
            inspector_id=inspector.id, version=1,
            estado=InspeccionEstadoEnum.solicitada, checklist={},
        )
        insp3 = Inspeccion(
            corredora_id=cid, activo_id=flota_pacifico.id, solicitud_id=None,
            inspector_id=inspector.id, version=2,
            estado=InspeccionEstadoEnum.validada,
            checklist={"titulo": "Checklist Flota", "items": []},
        )
        db.add_all([insp1, insp2, insp3])
        db.flush()
        counts["solicitud_inspeccion"] = 2
        counts["inspeccion"] = 3

        # --- Documentos / observaciones -----------------------------------
        db.add_all([
            Documento(
                corredora_id=cid, entidad_tipo="poliza", entidad_id=pol_flag.id,
                nombre="Póliza POL-2024-0072.pdf", tipo="poliza",
                url="https://docs.radal.cl/pol-2024-0072.pdf", version=1,
                autor_id=jose.id, compartido_con=["admin_corredora", "ejecutivo_corredora"],
                created_at=_dt(_days(-40)),
            ),
            Documento(
                corredora_id=cid, entidad_tipo="siniestro", entidad_id=sin1.id,
                nombre="Informe preliminar siniestro.pdf", tipo="informe",
                url="https://docs.radal.cl/sin-1-informe.pdf", version=1,
                autor_id=inspector.id, compartido_con=["admin_corredora"],
                created_at=_dt(_days(-50)),
            ),
        ])
        db.add_all([
            Observacion(
                corredora_id=cid, entidad_tipo="poliza", entidad_id=pol_flag.id,
                autor_id=jose.id,
                texto="Cliente solicita revisar límite de indemnización en renovación.",
                created_at=_dt(_days(-5)),
            ),
            Observacion(
                corredora_id=cid, entidad_tipo="cotizacion", entidad_id=cot1.id,
                autor_id=jose.id, texto="Prioridad alta: exportación con fecha comprometida.",
                created_at=_dt(_days(-3)),
            ),
        ])
        counts["documento"] = 2
        counts["observacion"] = 2

        # --- Actividades (spread across recent days) ----------------------
        actividades = [
            Actividad(corredora_id=cid, usuario_id=jose.id, accion="Cliente creado",
                      entidad_tipo="cliente", entidad_id=techlab.id,
                      descripcion="Se creó TechLab Innovaciones SpA (onboarding)",
                      created_at=_dt(_days(-1), 9, 15)),
            Actividad(corredora_id=cid, usuario_id=jose.id, accion="Oferta recibida",
                      entidad_tipo="cotizacion", entidad_id=cot3.id,
                      descripcion="Mapfre ofertó Bodega de guarda barricas",
                      created_at=_dt(_days(-1), 14, 2)),
            Actividad(corredora_id=cid, usuario_id=inspector.id,
                      accion="Inspección asociada", entidad_tipo="inspeccion",
                      entidad_id=insp1.id,
                      descripcion="Inspección en progreso — Planta Talca",
                      created_at=_dt(_days(-2), 11, 30)),
            Actividad(corredora_id=cid, usuario_id=admin.id, accion="Renovación iniciada",
                      entidad_tipo="renovacion", entidad_id=renovaciones[2].id,
                      descripcion="REN Flota Pacífico pasó a cotizando",
                      created_at=_dt(_days(-3), 16, 45)),
            Actividad(corredora_id=cid, usuario_id=jose.id, accion="Siniestro reportado",
                      entidad_tipo="siniestro", entidad_id=sin2.id,
                      descripcion="Colisión camión refrigerado Pacífico",
                      created_at=_dt(_days(-4), 8, 20)),
            Actividad(corredora_id=cid, usuario_id=jose.id, accion="Cotización enviada",
                      entidad_tipo="cotizacion", entidad_id=cot1.id,
                      descripcion="Carga refrigerada exportación enviada a aseguradoras",
                      created_at=_dt(_days(-6), 10, 5)),
            Actividad(corredora_id=cid, usuario_id=admin.id, accion="Póliza emitida",
                      entidad_tipo="poliza", entidad_id=pol10.id,
                      descripcion="POL-2025-0048 emitida para Constructora Andes",
                      created_at=_dt(_days(-8), 12, 0)),
            Actividad(corredora_id=cid, usuario_id=jose.id, accion="Observación agregada",
                      entidad_tipo="poliza", entidad_id=pol_flag.id,
                      descripcion="Revisar límite de indemnización POL-2024-0072",
                      created_at=_dt(_days(-5), 15, 10)),
            Actividad(corredora_id=cid, usuario_id=inspector.id,
                      accion="Inspección validada", entidad_tipo="inspeccion",
                      entidad_id=insp3.id,
                      descripcion="Inspección de flota Pacífico validada",
                      created_at=_dt(_days(-9), 13, 25)),
            Actividad(corredora_id=cid, usuario_id=jose.id, accion="Cliente creado",
                      entidad_tipo="cliente", entidad_id=minera.id,
                      descripcion="Prospecto Minera Cordillera Ltda.",
                      created_at=_dt(_days(-10), 9, 0)),
        ]
        db.add_all(actividades)
        counts["actividad"] = len(actividades)

        db.commit()

    return counts


def main() -> None:
    counts = seed()
    print("Radal demo dataset seeded:")
    for table, n in counts.items():
        print(f"  {table:28s} {n}")


if __name__ == "__main__":
    main()
