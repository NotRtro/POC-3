import os
import json
import uuid
from datetime import datetime
from fastapi import FastAPI, HTTPException, Depends, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from typing import List, Optional
import uvicorn
from pydantic import BaseModel

# MOCK
class S3Service:
    def __init__(self):
        self.storage = {}
    
    def upload_file(self, file_content, metadata):
        file_id = str(uuid.uuid4())
        self.storage[file_id] = {
            "content": file_content,
            "metadata": metadata
        }
        return file_id
    
    def get_file(self, file_id):
        if file_id in self.storage:
            return self.storage[file_id]
        return None

# Database 
SQLALCHEMY_DATABASE_URL = "sqlite:///./mendoza_law_firm.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models for different services
class Cliente(Base):
    __tablename__ = "clientes"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, index=True)
    email = Column(String, unique=True, index=True)
    telefono = Column(String)
    fecha_registro = Column(DateTime, default=datetime.now)

class Caso(Base):
    __tablename__ = "casos"
    id = Column(Integer, primary_key=True, index=True)
    referencia = Column(String, unique=True, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"))
    descripcion = Column(String)
    estado = Column(String)
    fecha_inicio = Column(DateTime, default=datetime.now)
    abogado_asignado = Column(String)

class Pago(Base):
    __tablename__ = "pagos"
    id = Column(Integer, primary_key=True, index=True)
    caso_id = Column(Integer, ForeignKey("casos.id"))
    monto = Column(Float)
    fecha = Column(DateTime, default=datetime.now)
    metodo = Column(String)
    recibo_ref = Column(String, unique=True)

class Documento(Base):
    __tablename__ = "documentos"
    id = Column(Integer, primary_key=True, index=True)
    caso_id = Column(Integer, ForeignKey("casos.id"))
    nombre = Column(String)
    tipo = Column(String)
    s3_key = Column(String)
    fecha_subida = Column(DateTime, default=datetime.now)

# Create tables
Base.metadata.create_all(bind=engine)

# Dependency for DB sessions
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

s3_service = S3Service()

# Pydantic models for API
class ClienteBase(BaseModel):
    nombre: str
    email: str
    telefono: Optional[str] = None

class ClienteCreate(ClienteBase):
    pass

class ClienteResponse(ClienteBase):
    id: int
    fecha_registro: datetime
    
    class Config:
        orm_mode = True

class CasoBase(BaseModel):
    referencia: str
    cliente_id: int
    descripcion: str
    estado: str
    abogado_asignado: str

class CasoCreate(CasoBase):
    pass

class CasoResponse(CasoBase):
    id: int
    fecha_inicio: datetime
    
    class Config:
        orm_mode = True

class PagoBase(BaseModel):
    caso_id: int
    monto: float
    metodo: str

class PagoCreate(PagoBase):
    pass

class PagoResponse(PagoBase):
    id: int
    fecha: datetime
    recibo_ref: str
    
    class Config:
        orm_mode = True

class DocumentoBase(BaseModel):
    caso_id: int
    nombre: str
    tipo: str

class DocumentoResponse(DocumentoBase):
    id: int
    fecha_subida: datetime
    
    class Config:
        orm_mode = True

# APP
app = FastAPI(title="Mendoza Law Firm API Gateway")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ServicioClientes
@app.post("/api/clientes/", response_model=ClienteResponse, tags=["Clientes"])
def crear_cliente(cliente: ClienteCreate, db: Session = Depends(get_db)):
    db_cliente = Cliente(**cliente.dict())
    db.add(db_cliente)
    db.commit()
    db.refresh(db_cliente)
    return db_cliente

@app.get("/api/clientes/", response_model=List[ClienteResponse], tags=["Clientes"])
def listar_clientes(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(Cliente).offset(skip).limit(limit).all()

@app.get("/api/clientes/{cliente_id}", response_model=ClienteResponse, tags=["Clientes"])
def obtener_cliente(cliente_id: int, db: Session = Depends(get_db)):
    cliente = db.query(Cliente).filter(Cliente.id == cliente_id).first()
    if cliente is None:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    return cliente

# ServicioCasos
@app.post("/api/casos/", response_model=CasoResponse, tags=["Casos"])
def crear_caso(caso: CasoCreate, db: Session = Depends(get_db)):
    # Verificar que el cliente existe
    cliente = db.query(Cliente).filter(Cliente.id == caso.cliente_id).first()
    if cliente is None:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    
    # Crear referencia única basada en timestamp y cliente
    if not caso.referencia:
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        caso.referencia = f"CASO-{caso.cliente_id}-{ts}"
        
    db_caso = Caso(**caso.dict())
    db.add(db_caso)
    db.commit()
    db.refresh(db_caso)
    return db_caso

@app.get("/api/casos/", response_model=List[CasoResponse], tags=["Casos"])
def listar_casos(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(Caso).offset(skip).limit(limit).all()

@app.get("/api/casos/{caso_id}", response_model=CasoResponse, tags=["Casos"])
def obtener_caso(caso_id: int, db: Session = Depends(get_db)):
    caso = db.query(Caso).filter(Caso.id == caso_id).first()
    if caso is None:
        raise HTTPException(status_code=404, detail="Caso no encontrado")
    return caso

# ServicioPagos
@app.post("/api/pagos/", response_model=PagoResponse, tags=["Pagos"])
def registrar_pago(pago: PagoCreate, db: Session = Depends(get_db)):
    # Verificar que el caso existe
    caso = db.query(Caso).filter(Caso.id == pago.caso_id).first()
    if caso is None:
        raise HTTPException(status_code=404, detail="Caso no encontrado")
    
    # Generar referencia de recibo única
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    recibo_ref = f"REC-{pago.caso_id}-{ts}"
    
    db_pago = Pago(**pago.dict(), recibo_ref=recibo_ref)
    db.add(db_pago)
    db.commit()
    db.refresh(db_pago)
    return db_pago

@app.get("/api/pagos/", response_model=List[PagoResponse], tags=["Pagos"])
def listar_pagos(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return db.query(Pago).offset(skip).limit(limit).all()

# ServicioDocumentos
@app.post("/api/documentos/", tags=["Documentos"])
async def subir_documento(
    caso_id: int, 
    nombre: str, 
    tipo: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    # Verificar que el caso existe
    caso = db.query(Caso).filter(Caso.id == caso_id).first()
    if caso is None:
        raise HTTPException(status_code=404, detail="Caso no encontrado")
    
    # Leer archivo y almacenar en nuestro servicio tipo S3
    file_content = await file.read()
    metadata = {
        "caso_id": caso_id,
        "nombre": nombre,
        "tipo": tipo,
        "filename": file.filename,
        "content_type": file.content_type
    }
    
    s3_key = s3_service.upload_file(file_content, metadata)
    
    # Guardar referencia en la base de datos
    db_documento = Documento(
        caso_id=caso_id,
        nombre=nombre,
        tipo=tipo,
        s3_key=s3_key
    )
    db.add(db_documento)
    db.commit()
    db.refresh(db_documento)
    
    return {
        "id": db_documento.id,
        "caso_id": db_documento.caso_id,
        "nombre": db_documento.nombre,
        "tipo": db_documento.tipo,
        "fecha_subida": db_documento.fecha_subida
    }

@app.get("/api/documentos/caso/{caso_id}", tags=["Documentos"])
def listar_documentos_caso(caso_id: int, db: Session = Depends(get_db)):
    # Verificar que el caso existe
    caso = db.query(Caso).filter(Caso.id == caso_id).first()
    if caso is None:
        raise HTTPException(status_code=404, detail="Caso no encontrado")
    
    documentos = db.query(Documento).filter(Documento.caso_id == caso_id).all()
    return documentos

# ServicioReportes
@app.get("/api/reportes/casos-por-abogado", tags=["Reportes"])
def reporte_casos_por_abogado(db: Session = Depends(get_db)):
    resultado = {}
    casos = db.query(Caso).all()
    
    for caso in casos:
        if caso.abogado_asignado not in resultado:
            resultado[caso.abogado_asignado] = 0
        resultado[caso.abogado_asignado] += 1
    
    return resultado

@app.get("/api/reportes/pagos-por-periodo", tags=["Reportes"])
def reporte_pagos_por_periodo(desde: str, hasta: str, db: Session = Depends(get_db)):
    # Convertir fechas
    try:
        fecha_desde = datetime.strptime(desde, "%Y-%m-%d")
        fecha_hasta = datetime.strptime(hasta, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato de fecha inválido. Usar YYYY-MM-DD")
    
    # Filtrar pagos por período
    pagos = db.query(Pago).filter(Pago.fecha >= fecha_desde, Pago.fecha <= fecha_hasta).all()
    
    # Calcular total
    total = sum(pago.monto for pago in pagos)
    
    return {
        "periodo": {"desde": desde, "hasta": hasta},
        "total_pagos": len(pagos),
        "monto_total": total,
        "pagos": pagos
    }

# ServicioBusqueda
@app.get("/api/busqueda/", tags=["Búsqueda"])
def busqueda_general(termino: str, db: Session = Depends(get_db)):
    # Búsqueda en múltiples entidades
    clientes = db.query(Cliente).filter(Cliente.nombre.contains(termino)).all()
    casos = db.query(Caso).filter(
        (Caso.referencia.contains(termino)) | (Caso.descripcion.contains(termino))
    ).all()
    documentos = db.query(Documento).filter(Documento.nombre.contains(termino)).all()
    
    return {
        "clientes": clientes,
        "casos": casos,
        "documentos": documentos
    }

# Run the application
if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)