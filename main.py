from fastapi import FastAPI, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from typing import List, Optional
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from bson import ObjectId
from pydantic_settings import BaseSettings
import logging

logging.basicConfig(level=logging.DEBUG)

# ---------------------------
# Settings
# ---------------------------
class Settings(BaseSettings):
    mongodb_uri: str
    database_name: str = "hroone_db"

    class Config:
        env_file = ".env"

settings = Settings()

# ---------------------------
# Database Connection & Indexes
# ---------------------------
client: AsyncIOMotorClient = AsyncIOMotorClient(settings.mongodb_uri)
db: AsyncIOMotorDatabase = client[settings.database_name]

async def create_indexes():
    # Text index on name for optimized search, and single-field indexes for filters
    await db.products.create_index([("name", "text")], background=True)
    await db.products.create_index([("size", 1)], background=True)
    await db.orders.create_index([("user_id", 1)], background=True)

# ---------------------------
# Data Models
# ---------------------------
class ProductIn(BaseModel):
    name: str = Field(..., min_length=1)
    description: Optional[str] = None
    price: float = Field(..., gt=0)
    size: Optional[str]

    def to_lowercase(self):
        return ProductIn(
            name=self.name.lower(),
            description=self.description.lower() if self.description else None,
            price=self.price,
            size=self.size.lower() if self.size else None
        )

class ProductOut(ProductIn):
    id: str = Field(alias="_id")

    class Config:
        allow_population_by_field_name = True
        allow_population_by_alias = True

class OrderItem(BaseModel):
    product_id: str
    quantity: int = Field(..., gt=0)

class OrderIn(BaseModel):
    user_id: str = Field(..., min_length=1)
    items: List[OrderItem]

    def to_lowercase(self):
        return OrderIn(
            user_id=self.user_id.lower(),
            items=[OrderItem(product_id=item.product_id.lower(), quantity=item.quantity) for item in self.items]
        )

class OrderOut(OrderIn):
    id: str = Field(alias="_id")
    total: float

    class Config:
        allow_population_by_field_name = True
        allow_population_by_alias = True

# ---------------------------
# FastAPI App Initialization
# ---------------------------
app = FastAPI(debug=True, on_startup=[create_indexes])

# Dependency to get DB
async def get_db() -> AsyncIOMotorDatabase:
    return db

# ---------------------------
# Create Product API
# ---------------------------
@app.post("/products", status_code=201, response_model=ProductOut)
async def create_product(
    product: ProductIn,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    product = product.to_lowercase()
    doc = product.dict()
    result = await db.products.insert_one(doc)
    doc["_id"] = str(result.inserted_id)
    return doc

# ---------------------------
# List Products API
# ---------------------------
@app.get("/products", response_model=List[ProductOut])
async def list_products(
    name: Optional[str] = Query(None),
    size: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    query = {}
    if name:
        query["name"] = {"$regex": name, "$options": "i"}
    if size:
        query["size"] = size.lower()

    cursor = (
        db.products
        .find(query)
        .sort("_id", 1)
        .skip(offset)
        .limit(limit)
    )
    products = await cursor.to_list(length=limit)
    for p in products:
        p["_id"] = str(p["_id"])
    return products

# ---------------------------
# Create Order API
# ---------------------------
@app.post("/orders", status_code=201, response_model=OrderOut)
async def create_order(
    order: OrderIn,
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    order = order.to_lowercase()
    # Validate products and compute total price
    product_ids = [ObjectId(item.product_id) for item in order.items]
    products = await db.products.find({"_id": {"$in": product_ids}}).to_list(length=len(product_ids))
    price_map = {str(p["_id"]): p["price"] for p in products}
    if len(price_map) != len(product_ids):
        raise HTTPException(status_code=400, detail="One or more products not found")

    total = sum(price_map[item.product_id] * item.quantity for item in order.items)
    doc = order.dict(by_alias=True)
    doc["items"] = [{"product_id": item.product_id, "quantity": item.quantity} for item in order.items]
    doc["total"] = total
    result = await db.orders.insert_one(doc)
    doc["_id"] = str(result.inserted_id)
    return doc

# ---------------------------
# Get Orders by User API
# ---------------------------
@app.get("/orders/{user_id}", response_model=List[OrderOut])
async def list_orders(
    user_id: str,
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncIOMotorDatabase = Depends(get_db)
):
    cursor = (
        db.orders
        .find({"user_id": user_id.lower()})
        .sort("_id", 1)
        .skip(offset)
        .limit(limit)
    )
    orders = await cursor.to_list(length=limit)
    for o in orders:
        o["_id"] = str(o["_id"])
        for item in o["items"]:
            item["product_id"] = str(item["product_id"])
    return orders

