"""AISI 304: Sandvik group M1.0 and kc1 1800 from the Turning tools 2020 catalogue (mc not verified)

Data only: the seed material "AISI 304" gets its catalogue group and kc1 when its kc1 source is still the seed's
placeholder (a value the operator changed is left as it is). TT 2020 H30 (PDF 684), the material cross reference list:
X5CrNi18-10 / 1.4301 / AISI 304 = M1.1.Z.AQ, CMC 05.21; the cutting data tables (A278, B130, C73) give CMC 05.21 as
M1.0.Z.AQ "Austenitic", kc1 1800 N/mm², HB 180 (A278, PDF 282). The user chose M1.0 (2026-10-25). mc is not in the
catalogues' text: kept, "not verified".

Revision ID: c1e5a9d3f7b2
Revises: b9d3f7a1c5e2
Create Date: 2026-10-25 16:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c1e5a9d3f7b2'
down_revision = 'b9d3f7a1c5e2'
branch_labels = None
depends_on = None

PLACEHOLDER = ("PLACEHOLDER: catalogue-type value for ISO M austenitic stainless steel (e.g. Sandvik Coromant M2), "
               "not verified")
KC1_SOURCE = ("Sandvik TT 2020 A278 (PDF 282), M1.0.Z.AQ (CMC 05.21, Austenitic, HB 180); group M1.0: TT cross "
              "reference list H30 (PDF 684), X5CrNi18-10 / 1.4301 = M1.1.Z.AQ, CMC 05.21, given as M1.0.Z.AQ in the "
              "cutting data tables")
MC_SOURCE = "not verified (not in the TT / SRT 2020 text)"
material = sa.table("material", sa.column("id", sa.Integer), sa.column("name", sa.String), sa.column("kc1", sa.Float),
                    sa.column("kc1_source", sa.String), sa.column("mc_source", sa.String),
                    sa.column("catalogue_group", sa.String), sa.column("hardness_hb", sa.Integer))


def upgrade():
    op.get_bind().execute(material.update().where(material.c.name == "AISI 304")
                          .where(material.c.kc1_source == PLACEHOLDER)
                          .values(kc1=1800, kc1_source=KC1_SOURCE, mc_source=MC_SOURCE, catalogue_group="M1.0",
                                  hardness_hb=180))


def downgrade():
    op.get_bind().execute(material.update().where(material.c.name == "AISI 304")
                          .where(material.c.kc1_source == KC1_SOURCE)
                          .values(kc1=2000, kc1_source=PLACEHOLDER, mc_source=PLACEHOLDER, catalogue_group=None))
