/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

const CTRL = "delivery.evidence.control";

const TABS = [
    { id: "all", label: "Todas" },
    { id: "pending", label: "Por entregar" },
    { id: "no_evidence", label: "Sin evidencia" },
    { id: "ready", label: "Listas" },
    { id: "sent", label: "Enviadas" },
    { id: "review", label: "Revisión" },
];

const DELIVERY_LABELS = {
    pending: "Pendiente", partial: "Parcial", delivered: "Entregado",
    review: "Revisión", cancelled: "Cancelado",
};
const DOC_LABELS = {
    no_evidence: "Sin evidencia", partial_evidence: "Evidencia incompleta",
    evidence_received: "Evidencia recibida", ready: "Lista p/ Admón.",
    sent: "Enviada",
};

/**
 * Entregas y Evidencias — Centro de operación.
 *
 * Flujo en pantalla: (1) las ventas confirmadas entran solas o con
 * Sincronizar; (2) se les cargan y validan evidencias; (3) se marcan
 * listas; (4) se genera la relación y se marcan enviadas. El Excel de
 * Administración se puede subir para palomear en lote.
 */
export class DeliveryEvidenceApp extends Component {
    static template = "restricciones_entregas.EvidenceApp";
    static props = { "*": true };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            loading: true,
            bootstrap: null,
            tab: "all",
            search: "",
            rows: [],
            detail: null,      // control abierto en el panel
            busy: false,
            uploadType: "remision_firmada",
            excel: { open: false, matches: [], selected: {}, scanning: false, fileName: "" },
            confirm: null,     // acción en confirmación de dos pasos
        });

        onWillStart(async () => {
            await this.reloadBootstrap();
            await this.reloadList();
            this.state.loading = false;
        });
    }

    // ------------------------------------------------------------------
    // Carga
    // ------------------------------------------------------------------
    async reloadBootstrap() {
        this.state.bootstrap = await this.orm.call(CTRL, "js_bootstrap", []);
    }

    async reloadList() {
        this.state.rows = await this.orm.call(CTRL, "js_list", [
            this.state.tab, this.state.search,
        ]);
    }

    async setTab(tab) {
        this.state.tab = tab;
        this.state.detail = null;
        await this.reloadList();
    }

    async onSearch(ev) {
        this.state.search = ev.target.value;
        await this.reloadList();
    }

    get tabs() {
        return TABS.map((t) => ({
            ...t,
            count: this.state.bootstrap.counts[t.id] ?? 0,
        }));
    }

    deliveryLabel(state) { return DELIVERY_LABELS[state] || state; }
    docLabel(state) { return DOC_LABELS[state] || state; }

    pctStyle(row) {
        return `width:${Math.max(0, Math.min(row.pct || 0, 100))}%`;
    }

    money(row) {
        try {
            return new Intl.NumberFormat("es-MX", {
                style: "currency", currency: row.currency || "MXN",
            }).format(row.amount_total);
        } catch {
            return `${row.amount_total} ${row.currency}`;
        }
    }

    rowClass(row) {
        if (row.doc_state === "sent") return "deva-row-sent";
        if (row.doc_state === "ready") return "deva-row-ready";
        if (row.delivery_state === "review") return "deva-row-review";
        if (row.delivery_state === "delivered" &&
            ["no_evidence", "partial_evidence"].includes(row.doc_state)) return "deva-row-warn";
        if (["pending", "partial"].includes(row.delivery_state)) return "deva-row-pending";
        return "";
    }

    _notifyError(error) {
        const message =
            error?.data?.message || error?.message?.data?.message || error?.message ||
            "Ocurrió un error inesperado.";
        this.notification.add(String(message), { type: "danger", sticky: true });
    }

    // ------------------------------------------------------------------
    // Detalle
    // ------------------------------------------------------------------
    async openDetail(row) {
        this.state.detail = await this.orm.call(CTRL, "js_detail", [[row.id]]);
    }

    closeDetail() {
        this.state.detail = null;
    }

    async _refreshAll() {
        await Promise.all([this.reloadBootstrap(), this.reloadList()]);
        if (this.state.detail) {
            this.state.detail = await this.orm.call(CTRL, "js_detail", [[this.state.detail.id]]);
        }
    }

    async runAction(actionName) {
        if (this.state.busy) return;
        this.state.busy = true;
        try {
            this.state.detail = await this.orm.call(CTRL, "js_action", [
                [this.state.detail.id], actionName,
            ]);
            await Promise.all([this.reloadBootstrap(), this.reloadList()]);
        } catch (error) {
            this._notifyError(error);
        } finally {
            this.state.busy = false;
        }
    }

    async validateDocument(doc) {
        try {
            this.state.detail = await this.orm.call(CTRL, "js_validate_document", [
                [this.state.detail.id], doc.id,
            ]);
        } catch (error) {
            this._notifyError(error);
        }
    }

    async saveNotes(ev) {
        await this.orm.call(CTRL, "js_set_notes", [[this.state.detail.id], ev.target.value]);
        this.state.detail.notes = ev.target.value;
    }

    async openSaleForm() {
        // Abre la orden de venta del expediente en la vista estándar.
        const [control] = await this.orm.read(CTRL, [this.state.detail.id], ["sale_order_id"]);
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "sale.order",
            res_id: control.sale_order_id[0],
            views: [[false, "form"]],
        });
    }

    async saveCompact(field, ev) {
        try {
            this.state.detail = await this.orm.call(CTRL, "js_set_compact", [
                [this.state.detail.id], { [field]: ev.target.value },
            ]);
            await this.reloadList();
        } catch (error) {
            this._notifyError(error);
        }
    }

    generateReport() {
        const ids = this.state.detail ? [this.state.detail.id] : [];
        this.action.doAction("restricciones_entregas.action_delivery_evidence_report_wizard", {
            additionalContext: ids.length ? { default_control_ids: [[6, 0, ids]] } : {},
        });
    }

    // ------------------------------------------------------------------
    // Evidencias (subida directa)
    // ------------------------------------------------------------------
    triggerUpload(ev) {
        ev.target.closest(".deva-upload").querySelector("input[type=file]").click();
    }

    async onFileSelected(ev) {
        const file = ev.target.files && ev.target.files[0];
        ev.target.value = "";
        if (!file) return;
        const b64 = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result.split(",")[1]);
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });
        try {
            this.state.detail = await this.orm.call(CTRL, "js_add_evidence", [
                [this.state.detail.id],
                {
                    file: b64,
                    file_name: file.name,
                    name: file.name.replace(/\.[^.]+$/, ""),
                    evidence_type: this.state.uploadType,
                },
            ]);
            await this.reloadBootstrap();
            this.notification.add("Evidencia cargada.", { type: "success" });
        } catch (error) {
            this._notifyError(error);
        }
    }

    // ------------------------------------------------------------------
    // Excel: subir y palomear en lote
    // ------------------------------------------------------------------
    openExcel() {
        this.state.excel = {
            open: true, matches: [], selected: {}, scanning: false,
            fileName: "", diagnostics: [],
        };
    }

    async onExcelSelected(ev) {
        const file = ev.target.files && ev.target.files[0];
        ev.target.value = "";
        if (!file) return;
        this.state.excel.scanning = true;
        this.state.excel.fileName = file.name;
        const b64 = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result.split(",")[1]);
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });
        try {
            const result = await this.orm.call(CTRL, "js_match_excel", [b64, file.name]);
            this.state.excel.matches = result.matched;
            this.state.excel.diagnostics = result.diagnostics || [];
            this.state.excel.selected = Object.fromEntries(
                result.matched.map((m) => [m.id, true]));
            if (result.matched.length) {
                await this._refreshAll();
            }
        } catch (error) {
            this._notifyError(error);
        } finally {
            this.state.excel.scanning = false;
        }
    }

    toggleExcelRow(row) {
        this.state.excel.selected[row.id] = !this.state.excel.selected[row.id];
    }

    get excelSelectedIds() {
        return this.state.excel.matches
            .filter((m) => this.state.excel.selected[m.id])
            .map((m) => m.id);
    }

    async excelBulk(actionName) {
        const key = `excel-${actionName}`;
        if (this.state.confirm !== key) {
            this.state.confirm = key;
            setTimeout(() => {
                if (this.state.confirm === key) this.state.confirm = null;
            }, 3500);
            return;
        }
        this.state.confirm = null;
        const ids = this.excelSelectedIds;
        if (!ids.length) return;
        try {
            const result = await this.orm.call(CTRL, "js_bulk_action", [ids, actionName]);
            let message = `${result.ok.length} controles actualizados.`;
            if (result.failed.length) {
                message += " Fallaron: " + result.failed
                    .map((f) => `${f.name} (${f.reason})`).join("; ");
            }
            this.notification.add(message, {
                type: result.failed.length ? "warning" : "success", sticky: !!result.failed.length,
            });
            this.state.excel.open = false;
            await this._refreshAll();
        } catch (error) {
            this._notifyError(error);
        }
    }

    // ------------------------------------------------------------------
    // Sincronización rápida
    // ------------------------------------------------------------------
    async syncRecent() {
        if (this.state.busy) return;
        this.state.busy = true;
        try {
            const stats = await this.orm.call(CTRL, "js_sync_recent", [60]);
            this.notification.add(
                `Sincronización (últimos 60 días): ${stats.total} ventas — ` +
                `${stats.created} nuevas, ${stats.updated} actualizadas, ` +
                `${stats.review} requieren revisión.`,
                { type: "success" });
            await this._refreshAll();
        } catch (error) {
            this._notifyError(error);
        } finally {
            this.state.busy = false;
        }
    }
}

registry.category("actions").add("delivery_evidence_app", DeliveryEvidenceApp);
